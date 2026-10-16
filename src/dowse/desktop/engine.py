"""Search for the interface: queries in, ranked results out, on the caller's thread (an HTTP request's).

- The query model (the text part, ~310 MB, and graphics memory on a GPU) is there while search is used: it wakes when
  a window opens (`warm`) or a real query comes, and sleeps after `idle` seconds without one. Waking takes ~2 s;
  until it's up, searches use words alone (`Answer.meaning` False), and the interface asks again once
  `on_state("ready")` says so.
- The index lives in memory (`Searcher`) and is rebuilt in the background when indexing changed it (`reload`), then
  swapped in between queries.
- More like this (`like`) needs no model for a file of the index: its vectors are there. A file the index doesn't hold
  (dropped on the window, or Explorer's "Find similar") is read as indexing reads it, by the whole model (pictures
  and sound too), which wakes for it and sleeps after `EXAMPLE_IDLE` seconds.
"""

import collections
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .. import dates, modelfiles, paths, probes, reading, rules, runtime, search_controls
from ..embedder import Embedder
from ..indexer import Indexer
from ..rules import Kind
from ..searcher import LIKE_VECTORS, Hit, Searcher, for_meaning, strong_count
from ..store import FileRow, Store, default_file


@dataclass
class Answer:
    query: str
    hits: list[Hit]
    strong: int          # how many of `hits` are strong matches (the rest are "other matches")
    meaning: bool        # False: words only (the model wasn't awake)
    milliseconds: float
    when: str | None = None   # the time the query named and the results are kept to: "summer 2026"
    filters: list[dict] = field(default_factory=list)


class Unreadable(Exception):
    """A file that can't stand for a search: a kind Dowse doesn't read, or one the chosen model can't."""


class SearchEngine:
    IDLE = 600.0   # seconds without a query before the model sleeps
    EXAMPLE_IDLE = 120.0   # seconds after reading a file for "more like this" before the whole model sleeps
    EXAMPLE_SECONDS = 8.0  # how long reading such a file may take before what's read so far stands for it

    def __init__(self, choice: runtime.Choice | None, idle: float = IDLE,
                 on_state: Callable[[str], None] = lambda state: None, *, model: str | modelfiles.Model | None = None):
        self.choice, self.idle, self.on_state = choice, idle, on_state
        self.model = modelfiles.get(model)
        self.state = "asleep" if choice is not None else "unavailable"   # asleep | loading | ready | unavailable
        self._lock = threading.Lock()          # the model's state and handles
        self._search_lock = threading.Lock()   # the searcher (one query or swap at a time)
        self._searcher: Searcher | None = None
        self._stale = False
        self._refresh_state = threading.Lock()   # `_stale` and `_refreshing`
        self._refreshing = False
        self._refreshed = 0.0
        self._server: runtime.Server | None = None
        self._embedder: Embedder | None = None
        self._last_used = time.monotonic()
        self._example_lock = threading.Lock()   # the whole model, for files the index doesn't hold
        self._example_server: runtime.Server | None = None
        self._example_embedder: Embedder | None = None
        self._example_used = 0.0
        self._examples: collections.OrderedDict[tuple, np.ndarray] = collections.OrderedDict()
        self._stop = threading.Event()
        self._loader: threading.Thread | None = None
        threading.Thread(target=self._janitor, name="query model sleep", daemon=True).start()

    # Control (any thread)

    def warm(self) -> None:
        """Search is about to be used: the model wakes if it sleeps (or failed: the GPU may have been busy)."""
        with self._lock:
            self._last_used = time.monotonic()
            if self.choice is None or self.state not in ("asleep", "unavailable") or self._stop.is_set():
                return
            self.state = "loading"
            self._loader = threading.Thread(target=self._load_model, name="query model", daemon=True)
        self.on_state("loading")
        self._loader.start()

    REFRESH_EVERY = 3.0   # seconds between searchers made while the index keeps changing (files being read)

    def reload(self) -> None:
        """The index changed: a fresh searcher is made in the background and swapped in; searches go on with the one
        there meanwhile. Changes in a row make one, at most every `REFRESH_EVERY` seconds. (Made on the search's
        thread, under its lock, each step of a long file being read held searches up to a second.)"""
        with self._refresh_state:
            self._stale = True
            if self._refreshing:
                return
            self._refreshing = True
        threading.Thread(target=self._refresh, name="searcher", daemon=True).start()

    def stop(self, wait: bool = False) -> None:
        self._stop.set()
        self._unload()
        self._unload_example()
        if wait and self._loader is not None and self._loader.ident is not None:
            self._loader.join(30)
            if self._loader.is_alive():
                raise RuntimeError("The previous query model is still closing. Try changing models again shortly.")

    def search(self, query: str, kinds: frozenset[Kind] = frozenset(), within: str | list[str] | None = None,
               limit: int = 60, *, ignored_filters: list[str] | None = None, date_range: dict | None = None) -> Answer:
        started = time.perf_counter()
        ignored = [] if ignored_filters is None else ignored_filters
        span, filters = search_controls.resolve(query, kinds, within, ignored, date_range)
        if not query.strip() and not (kinds or within or span):
            return Answer(query, [], 0, False, 0.0)
        if query.strip():
            self.warm()
        vector, meaning = None, False
        embedder = self._embedder
        if embedder is not None and query.strip():
            try:
                vector, meaning = embedder.query(for_meaning(query)), True
                self._last_used = time.monotonic()
            except Exception:
                vector = None
        with self._search_lock:
            searcher = self._ensure_searcher()
            if self._stop.is_set() or searcher.model_tag not in (None, self.model.vector_tag):
                vector, meaning = None, False   # never score the old model's query against a new model's vectors
            hits = searcher.search(query, vector, kinds=set(kinds) or None, limit=limit, prefix_last=True,
                                   within=within, infer_date="date" not in ignored,
                                   infer_kinds="kind" not in ignored, date_span=span)
        if span:
            for hit in hits:
                captured = dates.taken(hit.info.get("taken"))
                hit.evidence.append({"source": "date", "words": [], "dateSource": "taken" if captured else "modified",
                                     "date": datetime.fromtimestamp(captured or hit.mtime).strftime("%Y-%m-%d")})
        return Answer(query, hits, strong_count(hits), meaning, (time.perf_counter() - started) * 1000,
                      span.label if span else None, filters)

    def like(self, path: str, loc: float | None = None, kinds: frozenset[Kind] = frozenset(),
             within: str | list[str] | None = None, limit: int = 60, *, date_range: dict | None = None,
             key: tuple | None = None) -> Answer:
        """More like this: the files nearest to a file of the index (from its own vectors), to a place in it (`loc`: a
        page, a passage, seconds), or to a file the index doesn't hold, read for this (`key` names its content when
        `path` is a copy). Raises `Unreadable`."""
        started = time.perf_counter()
        span, filters = search_controls.resolve("", kinds, within, [], date_range)
        with self._search_lock:
            searcher = self._ensure_searcher()
            rows = searcher.rows_of(path, loc)
            example = searcher.vectors(rows) if len(rows) else None
        if example is None:
            example = self.read_example(path, key)
        with self._search_lock:
            searcher = self._ensure_searcher()
            if self._stop.is_set() or searcher.model_tag not in (None, self.model.vector_tag):
                raise Unreadable("The search model is changing. Try again in a moment.")
            hits = searcher.like(example, kinds=set(kinds) or None, limit=limit, exclude={path}, within=within,
                                 date_span=span)
        return Answer("", hits, strong_count(hits), True, (time.perf_counter() - started) * 1000,
                      span.label if span else None, filters)

    def read_example(self, path: str, key: tuple | None = None) -> np.ndarray:
        """Vectors standing for a file the index doesn't hold, read as indexing reads it (its first parts, for at
        most `EXAMPLE_SECONDS`), `LIKE_VECTORS` of its pieces at most, evenly spread. The last few are kept."""
        kind = rules.classify(path)
        if not isinstance(kind, Kind):
            raise Unreadable(f"Dowse doesn't read {kind or 'this kind of file'}.")
        if rules.cloud_only(rules.attributes(path)):
            raise Unreadable("This file is only online. Make it available offline to search with it.")
        try:
            facts = os.stat(path)
        except OSError as error:
            raise Unreadable("Dowse can't open this file.") from error
        key = key or (os.path.normcase(path), facts.st_size, facts.st_mtime)
        if key in self._examples:
            self._examples.move_to_end(key)
            return self._examples[key]
        if self.choice is None:
            raise Unreadable("Dowse isn't set up yet.")
        options = reading.Options.for_device(bool(self.choice.device))
        options.embed_media = self.model.media is not None
        options.photo_ocr = False   # words in a photo are for keyword search: no part of what it's like
        pieces, text_only, began = [], False, time.monotonic()
        try:
            for step in reading.steps(FileRow(0, path, kind, facts.st_size, facts.st_mtime, "pending", {}), options):
                text_only |= step.keyword_only
                pieces += [p for p in step.pieces if p.text is not None or p.part is not None]
                if len(pieces) >= 4 * LIKE_VECTORS or time.monotonic() - began > self.EXAMPLE_SECONDS:
                    break
        except Exception as error:   # a reader's own error, or a library's on a damaged file
            raise Unreadable(f"Dowse couldn't read this file: {error}") from error
        if not pieces:
            raise Unreadable("The chosen search model reads text only: choose one that sees pictures and hears sound "
                             "in Settings to search with this file." if text_only else "There's nothing in it to compare.")
        if len(pieces) > LIKE_VECTORS:
            pieces = [pieces[int(i)] for i in np.linspace(0, len(pieces) - 1, LIKE_VECTORS).round()]
        with self._example_lock:
            embedder = self._example_model()
            texts = [p.text for p in pieces if p.text is not None]
            parts = [p.part for p in pieces if p.part is not None]
            vectors = [*Indexer._embed_texts(embedder, texts), *(embedder.media(parts) if parts else [])]
            self._example_used = time.monotonic()
        vectors = [v for v in vectors if isinstance(v, np.ndarray)]
        if not vectors:
            raise Unreadable("The search model couldn't read this file.")
        example = np.stack(vectors).astype(np.float32)
        self._examples[key] = example
        while len(self._examples) > 8:
            self._examples.popitem(last=False)
        return example

    def _example_model(self) -> Embedder:
        """The whole model (pictures and sound too), woken for files read as examples (under `_example_lock`)."""
        if self._example_embedder is None:
            if self._stop.is_set():
                raise Unreadable("Dowse is closing.")
            server = runtime.Server(self.choice, slots=1, model=self.model, log=paths.logs() / "server-example.log")
            try:
                server.start(cancel=self._stop.is_set)
            except Exception as error:
                server.stop()
                raise Unreadable(f"The search model couldn't start: {error}") from error
            self._example_server, self._example_embedder = server, Embedder(server.url, parallel=1, model=self.model)
        return self._example_embedder

    def _unload_example(self) -> None:
        with self._example_lock:
            embedder, server, self._example_embedder, self._example_server = \
                self._example_embedder, self._example_server, None, None
        if embedder:
            embedder.close()
        if server:
            server.stop()

    def fit(self, text: str) -> dict[int, float]:
        """How far each file stands out for `text` as a search (an aisle's description, `Searcher.fit`); empty when
        the model isn't up within a few seconds."""
        embedder = self.embedder(wait=10.0)
        if embedder is None:
            return {}
        try:
            vector = embedder.query(text)
        except Exception:
            return {}
        with self._search_lock:
            searcher = self._ensure_searcher()
            if searcher.model_tag not in (None, self.model.vector_tag):
                return {}
            return searcher.fit(vector)

    def embedder(self, wait: float = 0.0) -> Embedder | None:
        """The query model's embedder: now, or within `wait` seconds of waking it; None if it isn't up by then."""
        if self._embedder is None and wait > 0:
            self.warm()
            deadline = time.monotonic() + wait
            while self._embedder is None and self.state == "loading" and time.monotonic() < deadline:
                time.sleep(0.1)
        if self._embedder is not None:
            self._last_used = time.monotonic()
        return self._embedder

    def store(self) -> Store:
        """The searcher's own connection, for reading what a hit points at (under the search lock)."""
        return self._ensure_searcher().store

    # Background

    def _ensure_searcher(self) -> Searcher:
        """The searcher (under the search lock): made here only the first time; after that, `_refresh` keeps it."""
        if self._searcher is None:
            self._searcher = Searcher(Store(default_file()))
        return self._searcher

    def _refresh(self) -> None:
        while True:
            pause = self._refreshed + self.REFRESH_EVERY - time.monotonic()
            if pause > 0 and self._stop.wait(pause):
                break
            with self._refresh_state:
                self._stale = False
            try:
                fresh = Searcher(Store(default_file()))   # (outside the lock: searches use the old one meanwhile)
            except Exception:
                fresh = None
            if fresh is not None:
                with self._search_lock:
                    old, self._searcher = self._searcher, fresh
                if old is not None:
                    old.store.close()
            self._refreshed = time.monotonic()
            with self._refresh_state:
                if not self._stale or self._stop.is_set():
                    break
        with self._refresh_state:
            self._refreshing = False

    def _load_model(self) -> None:
        server, embedder, store = None, None, None
        try:
            server = runtime.Server(self.choice, text_only=True, model=self.model)
            with self._lock:
                if self._stop.is_set():
                    return
                self._server = server   # stop() can terminate it while start() is still waiting for readiness
            server.start(cancel=self._stop.is_set)
            embedder = Embedder(server.url, parallel=1, model=self.model)
            store = Store(default_file())
            if self._stop.is_set() or store.meta("vectors") not in (None, self.model.vector_tag):
                raise RuntimeError("The index's model changed while the query model was loading")
            fresh = probes.load(store) is None
            probes.ensure(store, embedder)
        except Exception:
            if embedder:
                embedder.close()
            if server:
                server.stop()
            with self._lock:
                if self._server is server:
                    self._server = None
            if not self._stop.is_set():
                self._set_state("unavailable")
            return
        finally:
            if store:
                store.close()
        with self._lock:
            stopping = self._stop.is_set()
            if not stopping:
                self._server, self._embedder = server, embedder
                self._last_used = time.monotonic()
        if stopping:   # quit while it was waking
            embedder.close()
            server.stop()
            return
        if fresh:      # the hub correction needs the probes just made
            self.reload()
        self._set_state("ready")

    def _unload(self) -> None:
        with self._lock:
            embedder, server, self._embedder, self._server = self._embedder, self._server, None, None
        if embedder:
            embedder.close()
        if server:
            server.stop()

    def _janitor(self) -> None:
        """No query for `idle` seconds: the model goes, with its memory (it wakes on the next search)."""
        while not self._stop.wait(min(self.idle, 30.0)):
            with self._lock:
                idle = self.state == "ready" and time.monotonic() - self._last_used >= self.idle
            if idle:
                self._unload()
                self._set_state("asleep")
            if self._example_embedder is not None and time.monotonic() - self._example_used >= self.EXAMPLE_IDLE \
                    and not self._example_lock.locked():
                self._unload_example()

    def _set_state(self, state: str) -> None:
        self.state = state
        self.on_state(state)
