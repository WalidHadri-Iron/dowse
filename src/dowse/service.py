"""Keeps an index in step with its folders, live: the watcher's changes in, the model started only while there's
reading to do.

    service = IndexService(store, roots, options, model_factory(choice), on_event=print)
    service.run(stop)          # until `stop` (a threading.Event) is set

The loop, every quarter second:
- changes that settled (`watcher.Changes`) are reconciled (`Indexer.update`). A burst (an unzip, a copy of a whole
  folder) and an overflow (changes Windows couldn't hold) get a crawl of their folder instead: one walk beats ten
  thousand lookups, and only a crawl spots dataset folders.
- waiting files are read, new arrivals first: a settled change interrupts a long file between its steps.
- the model starts on the first file to read and stops after `idle` seconds without any (it holds GPU memory).
- a folder that went away (an unplugged drive) is left as it is in the index, and looked for every 30 s; when it's
  back it's crawled again. Every `resync` seconds everything is crawled again anyway: no watcher is perfect.
"""

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import crawler, modelfiles, probes, runtime
from .embedder import Embedder
from .indexer import READABLE, Indexer, Progress, SyncSummary
from .reading import Options
from .selection import Selection
from .store import Store, writer_lock
from .watcher import Batch, Changes, FolderWatcher

ModelFactory = Callable[[], tuple[Embedder, Callable[[], None]]]   # () → (embedder, close)


@dataclass
class Event:
    kind: str                       # sync | change | chosen | read | model | folder | error
    message: str
    progress: Progress | None = None
    summary: SyncSummary | None = None   # for "chosen": what the user's change did


def model_factory(choice: runtime.Choice, model: str | modelfiles.Model | None = None) -> ModelFactory:
    """The real model: a llama-server on `choice`, and an embedder talking to it."""
    def make() -> tuple[Embedder, Callable[[], None]]:
        server = runtime.Server(choice, model=model).start()
        embedder = Embedder(server.url, parallel=server.slots, model=server.model)

        def close() -> None:
            embedder.close()
            server.stop()

        return embedder, close

    return make


class _Model:
    """The model while there's work: started on first need, closed after `idle` seconds without any."""

    def __init__(self, factory: ModelFactory, idle: float, clock: Callable[[], float]):
        self.factory, self.idle, self.clock = factory, idle, clock
        self.embedder: Embedder | None = None
        self._close: Callable[[], None] | None = None
        self.last_used = 0.0

    def get(self) -> tuple[Embedder, bool]:
        """(the embedder, whether it was just started)."""
        started = self.embedder is None
        if started:
            self.embedder, self._close = self.factory()
        self.last_used = self.clock()
        return self.embedder, started

    def close_if_idle(self) -> bool:
        if self.embedder is not None and self.clock() - self.last_used >= self.idle:
            self.close()
            return True
        return False

    def close(self) -> None:
        if self._close is not None:
            self._close()
        self.embedder, self._close = None, None


class IndexService:
    def __init__(self, store: Store, selection: Selection, options: Options, models: ModelFactory, *,
                 quiet: float = 2.0, idle: float = 90.0, resync: float = 1800.0, look_again: float = 30.0,
                 burst: int = 500, on_event: Callable[[Event], None] = lambda event: None,
                 clock: Callable[[], float] = time.monotonic):
        self.store, self.selection, self.options = store, selection, options
        self.indexer = Indexer(store, options)
        self.changes = Changes(quiet, clock)
        self.model = _Model(models, idle, clock)
        self.resync, self.look_again, self.burst = resync, look_again, burst
        self.on_event, self.clock = on_event, clock
        self.ready = threading.Event()   # set once the first crawl is done and the folders are watched
        self.paused = threading.Event()  # set: changes are still followed, but nothing is read (and the model goes)
        self._lock = threading.Lock()
        self._next: Selection | None = None   # a new selection, from the user, waiting for the loop
        self._again: list[str] = []           # files the user asked to be read again, waiting for the loop

    def set_selection(self, selection: Selection) -> None:
        """The user added or removed something: the loop applies just the difference, at its next turn (from any
        thread)."""
        with self._lock:
            self._next = selection

    def read_again(self, paths: list[str]) -> None:
        """The user asked for these files to be read again (one that failed, one read before a fix): at the loop's
        next turn, from any thread."""
        with self._lock:
            self._again += paths

    def _watch(self, watcher: FolderWatcher) -> None:
        watcher.watch(list(self.selection.folders), [os.path.dirname(f) for f in self.selection.files])

    def run(self, stop: threading.Event) -> None:
        with writer_lock(self.store):
            watcher = FolderWatcher(self.changes)
            try:
                self._watch(watcher)
                self._sync("catching up")
                self.ready.set()
                next_resync = self.clock() + self.resync
                next_look = self.clock() + self.look_again
                pause_until = 0.0
                while not stop.is_set():
                    try:
                        with self._lock:
                            chosen, self._next = self._next, None
                            again, self._again = self._again, []
                        if chosen is not None:
                            self._choose(chosen, watcher)
                        if again:
                            self._requeue(again)
                        if self.changes.ready():
                            self._apply(self.changes.take())
                        if self.clock() >= next_look:
                            next_look = self.clock() + self.look_again
                            self._look_for_missing(watcher)
                        if self.clock() >= next_resync:
                            next_resync = self.clock() + self.resync
                            self._sync("regular check")
                        if not self.paused.is_set() and self.clock() >= pause_until and self._has_work():
                            self._read(stop)
                        elif self.paused.is_set() and self.model.embedder is not None:
                            self.model.close()
                            self.on_event(Event("model", "model stopped (paused)"))
                        elif self.model.close_if_idle():
                            self.on_event(Event("model", "model stopped (idle)"))
                    except Exception as error:   # anything unexpected: report it, wait a minute, carry on
                        self.on_event(Event("error", f"{type(error).__name__}: {error}"))
                        pause_until = self.clock() + 60
                    stop.wait(0.25)
            finally:
                watcher.stop()
                self.model.close()

    def _choose(self, chosen: Selection, watcher: FolderWatcher) -> None:
        """Applies a new selection: only the paths whose choice changed are looked at again (an added folder is
        crawled, a removed one leaves the index), not everything."""
        before = self.selection
        changed = sorted(set(before.folders) ^ set(chosen.folders) | set(before.files) ^ set(chosen.files)
                         | set(before.removed) ^ set(chosen.removed) | set(before.inactive) ^ set(chosen.inactive))
        self.selection = chosen
        self._watch(watcher)
        summary = self.indexer.update(changed, chosen)
        if not (chosen.folders or chosen.files):
            self.model.close()
        self.on_event(Event("chosen", _describe(summary), summary=summary))

    def _requeue(self, paths: list[str]) -> None:
        with self.store.transaction():
            for path in paths:
                for stored, (file_id, size, mtime) in self.store.known_at(path).items():
                    if self.selection.decide(stored) is not None:
                        self.store.requeue(file_id, size, mtime)
        self.on_event(Event("change", f"{len(paths)} to read again"))

    def _sync(self, why: str) -> None:
        started = self.clock()
        summary = self.indexer.sync(self.selection)
        self.on_event(Event("sync", f"{why}: {_describe(summary)} ({self.clock() - started:.1f} s)"))

    def _apply(self, batch: Batch) -> None:
        for root in batch.lost:
            self.on_event(Event("folder", f"{root} went away: its files stay in the index until it's back"))
        again = set(batch.overflowed)   # changes Windows couldn't hold: that folder, looked at again whole
        if len(batch.paths) + len(batch.renames) > self.burst:
            folders = list(self.selection.folders)
            again |= {crawler.root_of(p, folders) for p in batch.paths + [n for _, n in batch.renames]} - {None}
        if again:
            started = self.clock()
            summary = self.indexer.update(sorted(again), self.selection)
            self.on_event(Event("sync", f"many changes at once: {_describe(summary)} "
                                        f"({self.clock() - started:.1f} s)"))
            return
        if batch.paths or batch.renames:
            summary = self.indexer.update(batch.paths, self.selection, batch.renames)
            if summary.added or summary.changed or summary.moved or summary.removed:
                self.on_event(Event("change", _describe(summary)))

    def _look_for_missing(self, watcher: FolderWatcher) -> None:
        back = [root for root in watcher.missing(list(self.selection.folders)) if os.path.isdir(root)]
        if back:
            self._watch(watcher)
            for root in back:
                self.on_event(Event("folder", f"{root} is back"))
            summary = self.indexer.update(back, self.selection)
            self.on_event(Event("sync", f"folder back: {_describe(summary)}"))

    def _has_work(self) -> bool:
        return bool(self.store.pending(1, READABLE) or self.store.partial())

    def _read(self, stop: threading.Event) -> None:
        embedder, started = self.model.get()
        if started:
            self.on_event(Event("model", "model started"))

        def progress(p: Progress) -> None:
            self.model.last_used = self.clock()
            self.on_event(Event("read", p.path, p))

        self.indexer.run(embedder, should_stop=lambda: stop.is_set() or self.paused.is_set() or self.changes.ready()
                         or self._next is not None or bool(self._again), on_progress=progress)
        probes.ensure(self.store, embedder)
        self.model.last_used = self.clock()


def _describe(summary: SyncSummary) -> str:
    parts = [f"{n} {what}" for n, what in [(summary.added, "new"), (summary.changed, "changed"),
                                           (summary.moved, "moved"), (summary.removed, "removed")] if n]
    return ", ".join(parts) or "nothing changed"
