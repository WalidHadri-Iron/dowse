"""Keeps the index in step with the chosen folders, then reads and embeds whatever is waiting.

Two passes. The first reads every waiting file's first step (`reading`): a picture whole, a long PDF's first 30 pages,
a recording's first minutes, so every file is searchable soon. The later pass reads the rest of long files, a step at
a time. Reading runs on worker threads ahead of the model, so the two overlap; every step is saved in one
transaction with the place the next one starts, so a run that stops loses nothing and the next goes on from there.
"""

import contextlib
import os
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import PurePath

import numpy as np

from . import crawler, foreground, modelfiles
from .embedder import Embedder, EmbedError
from .extract import ExtractError
from .fmt import clock  # noqa: F401  (re-exported for the CLI)
from .reading import READERS, Options, Piece, Step, steps
from .rules import Kind
from .selection import Selection, inside, norm
from .store import FileRow, Modality, NewSegment, Store

# What made the vectors: an index never mixes two. (Token budgets may differ between files: same space.)
VECTORS = f"{modelfiles.REPOSITORY}@{modelfiles.REVISION[:7]}/v1"

READABLE = (Kind.SCREENSHOT, Kind.IMAGE, Kind.PDF, Kind.DOC, Kind.AUDIO, Kind.VIDEO)

__all__ = ["Indexer", "Options", "Progress", "SyncSummary", "VECTORS"]


@dataclass
class SyncSummary:
    added: int = 0
    changed: int = 0
    moved: int = 0
    removed: int = 0
    found: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    datasets: dict[str, int] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)   # chosen by name, but can't be read: why


@dataclass
class Progress:
    done: int          # files through their first step (the later pass reports 0)
    total: int
    path: str
    state: str         # done | partial | skipped | failed
    detail: str | None = None


@dataclass
class _Read:
    file: FileRow
    step: Step | None = None
    error: Exception | None = None


Vector = np.ndarray | Exception | None   # a piece's vector, the error the model gave, or None (keywords only)


class _Ahead:
    """Reads a file's steps one ahead on a worker thread, so reading the next overlaps embedding this one."""

    def __init__(self, iterator: Iterator[Step], pool: ThreadPoolExecutor):
        self.iterator, self.pool = iterator, pool
        self.future: Future = pool.submit(next, iterator, None)

    def next(self) -> Step | None:
        step = self.future.result()
        self.future = self.pool.submit(next, self.iterator, None) if step is not None else None
        return step

    def close(self) -> None:
        if self.future is not None:
            with contextlib.suppress(Exception):
                self.future.result()   # the generator may be running: let it finish its step first
        self.iterator.close()


class Indexer:
    def __init__(self, store: Store, options: Options | None = None, workers: int | None = None):
        self.store = store
        self.options = options or Options()
        self.workers = workers or max(2, min(8, (os.cpu_count() or 4) // 2))

    # Keeping the index in step with the folders

    def sync(self, selection: Selection) -> SyncSummary:
        """Brings the whole index in line with `selection`: what's chosen and found is in, the rest goes (a folder no
        longer watched, a file removed). Except what's only away: files under a watched folder that isn't there (an
        unplugged drive), and files added by name whose folder isn't there, stay until it's back and says otherwise."""
        crawl = crawler.crawl(selection, self.options.skip)
        summary = SyncSummary(found=len(crawl.files), skipped=crawl.skipped, datasets=crawl.datasets,
                              refused=crawl.refused)
        away_roots = [norm(f) for f in selection.folders if not os.path.isdir(f)]
        away_named = {norm(f) for f in selection.files if not os.path.isdir(os.path.dirname(f))}

        def away(path: str) -> bool:
            target = norm(path)
            return target in away_named or any(inside(target, root) for root in away_roots)

        candidates = {path: entry for path, entry in self.store.known().items() if not away(path)
                      and not (selection.inactive_by(path) and selection.configured(path) is not None)}
        self._reconcile(crawl.files, candidates, summary, selection=selection)
        self._read_again_older()
        return summary

    def _read_again_older(self) -> int:
        """Files read before their kind's reader changed (`reading.READERS`) wait to be read again; what they hold
        stays searchable until their new read is saved."""
        with self.store.transaction():
            return sum(self.store.db.execute(
                "UPDATE files SET state = 'pending' WHERE kind = ? AND state IN ('done', 'partial') "
                "AND coalesce(json_extract(info, '$.reader'), 1) < ?", (kind.value, version)).rowcount
                for kind, version in READERS.items())

    def update(self, paths: list[str], selection: Selection, renames: list[tuple[str, str]] = ()) -> SyncSummary:
        """Reconciles just these paths (the watcher's changes, or the user's: "add this", "remove that"): each is a
        file or a folder, created, changed, deleted, added or removed, and gets the decision a full crawl would make.
        `renames` are (old, new) pairs Windows reported: the same file or folder under a new name, re-pathed, never
        embedded again."""
        summary = SyncSummary()
        found: dict[str, crawler.Found] = {}
        candidates: dict[str, tuple[int, int, float]] = {}   # what the index has at the paths looked at
        for path in {*paths, *(p for pair in renames for p in pair)}:
            path = os.path.abspath(path)
            candidates |= self.store.known_at(path)
            if os.path.isdir(path):
                crawled = crawler.crawl(selection, self.options.skip, within=path)
                found |= {f.path: f for f in crawled.files}
                summary.refused |= crawled.refused
            elif os.path.isfile(path):
                fate = crawler.check(path, selection, self.options.skip)
                if isinstance(fate, crawler.Found):
                    found[path] = fate
                elif fate and selection.decide(path) is not None:
                    summary.refused[path] = fate
        was: dict[str, str] = {}   # new path → old path, for every file a rename moved
        for old, new in renames:
            old, new = os.path.abspath(old), os.path.abspath(new)
            for path in found:
                if path == new:
                    was[path] = old
                elif path.startswith(new.rstrip("\\/") + os.sep):
                    was[path] = old.rstrip("\\/") + path[len(new.rstrip("\\/")):]
        summary.found = len(found)
        candidates = {p: row for p, row in candidates.items()
                      if not (selection.inactive_by(p) and selection.configured(p) is not None)}
        self._reconcile(list(found.values()), candidates, summary, was, selection=selection)
        return summary

    def _reconcile(self, found: list[crawler.Found], candidates: dict[str, tuple[int, int, float]],
                   summary: SyncSummary, was: dict[str, str] | None = None, *, selection: Selection | None = None) -> None:
        """Brings the index in line with `found`, the files that should be in it among the paths looked at, whose
        indexed files were `candidates` (path → id, size, mtime). Known files found unchanged stay; changed ones
        are read again; new ones are queued; the rest are gone. A new file that is a gone one elsewhere is
        re-pathed instead: when a rename says so (`was`: new path → old), or when it has the same name, size and
        time (a move Windows reports as a delete and a create, across folders it doesn't watch as one)."""
        was = was or {}
        found_paths = {f.path for f in found}
        gone = {p: entry for p, entry in candidates.items() if p not in found_paths}
        gone_path = {entry[0]: path for path, entry in gone.items()}   # id → path
        twins: dict[tuple[str, int, float], list[int]] = {}
        for path, (file_id, size, mtime) in gone.items():
            twins.setdefault((PurePath(path).name.lower(), size, mtime), []).append(file_id)
        moved: set[int] = set()
        with self.store.transaction():
            for f in found:
                if f.path in candidates:
                    file_id, size, mtime = candidates[f.path]
                    if size != f.size or abs(mtime - f.mtime) > 1e-3:
                        self.store.requeue(file_id, f.size, f.mtime)
                        summary.changed += 1
                    continue
                same = None
                if f.path in was and was[f.path] in gone:
                    same = gone[was[f.path]][0]
                else:
                    same = next((i for i in twins.get((PurePath(f.path).name.lower(), f.size, f.mtime), [])
                                 if i not in moved), None)
                if same is not None and same not in moved:
                    self.store.repath(same, f.path)
                    moved.add(same)
                    summary.moved += 1
                    _, size, mtime = gone[gone_path[same]]
                    if (size, mtime) != (f.size, f.mtime):   # renamed and changed
                        self.store.requeue(same, f.size, f.mtime)
                        summary.changed += 1
                else:
                    self.store.add_file(f.path, f.kind, f.size, f.mtime)
                    summary.added += 1
            removed = [file_id for file_id, _, _ in gone.values() if file_id not in moved]
            self.store.remove(removed)
            summary.removed = len(removed)
            if selection is not None:
                self.store.apply_selection(selection)

    # Reading and embedding

    def check_vectors(self, model: modelfiles.Model | None = None) -> None:
        expected = model.vector_tag if model else VECTORS
        made = self.store.meta("vectors")
        if made is None:
            self.store.set_meta("vectors", expected)
        elif made != expected:
            raise RuntimeError(f"this index holds vectors from {made}, not {expected}: choose the matching model or rebuild it")

    def run(self, embedder: Embedder, should_stop: Callable[[], bool] = lambda: False,
            on_progress: Callable[[Progress], None] = lambda _: None, limit: int | None = None) -> int:
        """The first pass over waiting files, then (without `limit`) the later pass over long files. Returns how many
        files went through their first step.

        `should_stop` is asked after each piece of work is saved, never before the first: a run always moves forward,
        so changes that keep arriving (a busy Downloads folder) can't keep reading from ever getting anywhere."""
        self.check_vectors(getattr(embedder, "model", None))
        queue = self.store.pending(limit, READABLE)
        with ThreadPoolExecutor(self.workers, thread_name_prefix="read") as pool:
            done = self._first_pass(queue, pool, embedder, should_stop, on_progress)
            if limit is None and not (queue and should_stop()):
                for file in self.store.partial():
                    self._later_pass(file, pool, embedder, should_stop, on_progress)
                    if should_stop():
                        break
        return done

    @staticmethod
    def _batches(queue: list[FileRow]) -> list[list[FileRow]]:
        """Pictures 8 at a time (the server embeds them in parallel), other files one by one."""
        jobs: list[list[FileRow]] = []
        pictures: list[FileRow] = []
        for file in queue:
            if file.kind in (Kind.IMAGE, Kind.SCREENSHOT):
                pictures.append(file)
                if len(pictures) == 8:
                    jobs.append(pictures)
                    pictures = []
                continue
            if pictures:
                jobs.append(pictures)
                pictures = []
            jobs.append([file])
        if pictures:
            jobs.append(pictures)
        return jobs

    def _first_pass(self, queue: list[FileRow], pool: ThreadPoolExecutor, embedder: Embedder, should_stop,
                    on_progress) -> int:
        jobs = self._batches(queue)
        lookahead = self.workers + 2   # a recording's step holds ~30 MB of sound: keep a few, not dozens
        futures: list[Future] = []
        done = next_job = 0
        while next_job < len(jobs) or futures:
            while next_job < len(jobs) and len(futures) < lookahead:
                futures.append(pool.submit(self._read_job, jobs[next_job]))
                next_job += 1
            reads = futures.pop(0).result()
            for file, state, detail in self._commit(reads, embedder, first=True):   # saved before stopping
                done += 1
                on_progress(Progress(done, len(queue), file.path, state, detail))
            if should_stop():
                for future in futures:
                    future.cancel()
                break
        return done

    def _later_pass(self, file: FileRow, pool: ThreadPoolExecutor, embedder: Embedder, should_stop,
                    on_progress) -> None:
        """The rest of a long file, from where it was left, a step at a time (at least one)."""
        start = file.info.get("read_to", 0)
        ahead = _Ahead(steps(file, self.options, start), pool)
        try:
            while True:
                try:
                    step = ahead.next()
                except Exception as error:   # ExtractError, or a reader bug: this file stops, the run goes on
                    # It can't be read further (damaged past some point): what was read stays searchable.
                    with self.store.transaction():
                        self._stop_reading(file, error)
                    on_progress(Progress(0, 0, file.path, "done", f"stopped: {error}"))
                    return
                if step is None:
                    return
                for _, state, detail in self._commit([_Read(file, step)], embedder, first=False):
                    on_progress(Progress(0, 0, file.path, state, detail))
                if should_stop():
                    return
        finally:
            ahead.close()

    def _read_job(self, files: list[FileRow]) -> list[_Read]:
        """A file's first step, on a worker thread."""
        reads = []
        for file in files:
            foreground.wait()   # the person first: reading steps aside while the interface is in use
            iterator = steps(file, self.options)
            try:
                reads.append(_Read(file, next(iterator)))
            except ExtractError as error:
                reads.append(_Read(file, error=error))
            except Exception as error:   # a reader bug must not stop the run: the file says what happened
                reads.append(_Read(file, error=ExtractError(f"{type(error).__name__}: {error}"[:200])))
            finally:
                iterator.close()
        return reads

    # Embedding and saving (on the calling thread)

    def _commit(self, reads: list[_Read], embedder: Embedder, first: bool) -> list[tuple[FileRow, str, str | None]]:
        ready = [r for r in reads if r.step is not None]
        vectors = iter(self._embed([piece for r in ready for piece in r.step.pieces], embedder))
        out = []
        with self.store.transaction():
            for r in reads:
                if r.step is None:
                    self.store.finish(r.file.id, "failed", str(r.error))
                    out.append((r.file, "failed", str(r.error)))
                else:
                    step_vectors = [next(vectors) for _ in r.step.pieces]
                    out.append((r.file, *self._save(r.file, r.step, step_vectors, first)))
        return out

    def _embed(self, pieces: list[Piece], embedder: Embedder) -> list[Vector]:
        """One vector (or error, or None) per piece: texts in batches, pictures and sound in parallel."""
        out: list[Vector] = [None] * len(pieces)
        texts = [i for i, p in enumerate(pieces) if p.text is not None]
        parts = [i for i, p in enumerate(pieces) if p.part is not None]
        for i, vector in zip(texts, self._embed_texts(embedder, [pieces[i].text for i in texts])):
            out[i] = vector
        if parts:
            for i, vector in zip(parts, embedder.media([pieces[i].part for i in parts])):
                out[i] = vector
        return out

    def _save(self, file: FileRow, step: Step, vectors: list[Vector], first: bool) -> tuple[str, str | None]:
        if first and not step.keep:
            # A model change preserves old keyword text until the file's replacement is committed atomically.
            self.store.db.execute("DELETE FROM segments WHERE file = ? AND kind != 'name'", (file.id,))
        segments, problems = [], []
        for piece, vector in zip(step.pieces, vectors):
            if isinstance(vector, Exception):
                problems.append(f"model: {vector}"[:300])
            embedded = isinstance(vector, np.ndarray)
            if not embedded and not piece.body:
                continue   # nothing to keep: no vector and no words
            segments.append(NewSegment(piece.kind, piece.modality if embedded else Modality.NONE,
                                       vector if embedded else None, piece.loc, piece.loc_end, body=piece.body))
        current = self.store.file(file.id)
        info = (current.info if current else {}) | step.info
        if step.keep and not segments:   # nothing to add: the file stays as it was read
            self.store.finish(file.id, "done", None, info)
            return "done", None
        if first and step.position is None and not segments:
            if step.keyword_only:
                self.store.finish(file.id, "done", step.detail, info)
                return "done", step.detail
            # Nothing came of the whole file: the model refused it, or there was nothing to read.
            state, detail = ("failed", problems[0]) if problems else ("skipped", step.detail or "nothing to read")
            self.store.finish(file.id, state, detail, info)
            return state, detail
        self.store.add_segments(file.id, segments)
        lost = f"{len(problems)} pieces the model couldn't read" if problems else None
        if step.position is not None:
            info["read_to"] = step.position
            self.store.finish(file.id, "partial", step.detail, info)
            return "partial", ", ".join(filter(None, [step.detail, lost]))
        info.pop("read_to", None)
        self.store.finish(file.id, "done", lost, info)
        return "done", ", ".join(filter(None, [step.detail, lost])) or None

    def _stop_reading(self, file: FileRow, error: Exception) -> None:
        current = self.store.file(file.id)
        info = current.info if current else {}
        where = info.pop("read_to", None)
        self.store.finish(file.id, "done", f"read to {where}, then: {error}"[:300], info)

    @staticmethod
    def _embed_texts(embedder: Embedder, texts: list[str]) -> list[np.ndarray | None]:
        """Vectors for texts; one too long for the model (a dense page in a script of many tokens) is cut in half
        until it fits, and a text the model refuses gets None (keyword search still has its words)."""
        if not texts:
            return []
        try:
            return list(embedder.texts(texts))
        except EmbedError:
            out: list[np.ndarray | None] = []
            for text in texts:
                vector = None
                while len(text) > 200:
                    try:
                        vector = embedder.texts([text])[0]
                        break
                    except EmbedError:
                        text = text[: len(text) // 2]
                out.append(vector)
            return out
