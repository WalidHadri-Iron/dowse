"""Live indexing for the app: `IndexService` on a background thread, its news turned into a status the interface and
the tray show ("Reading 120 of 3,400", "Up to date"), and a nudge to search when the index changed.

Indexing pauses on its own when Windows' Battery Saver is on, and (if the user asks) whenever the PC runs on battery.
A pause the user chose and a pause for power are kept apart: plugging in never undoes "Pause indexing".
"""

import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass

from .. import modelfiles, runtime
from . import winapi
from ..reading import Options
from ..indexer import Indexer
from ..rules import NOTHING, Skip
from ..selection import Selection
from ..service import Event, IndexService, model_factory
from ..store import Busy, Store, default_file, writer_lock


@dataclass(frozen=True)
class Status:
    state: str          # starting | reading | idle | paused | waiting | off | error
    text: str
    done: int = 0
    total: int = 0

    def json(self) -> dict:
        return {"state": self.state, "text": self.text, "done": self.done, "total": self.total}


@dataclass(frozen=True)
class Config:
    choice: runtime.Choice | None
    selection: Selection
    plugged_in_only: bool = False
    skip: Skip = NOTHING
    model: str = modelfiles.DEFAULT

    def options(self) -> Options:
        options = Options.for_device(self.choice is not None and self.choice.device is not None, self.skip)
        options.embed_media = modelfiles.get(self.model).media is not None
        return options

    @property
    def anything(self) -> bool:
        return bool(self.selection.folders or self.selection.files)


class Indexing:
    RETRY = 15.0   # seconds between tries while another Dowse holds the index
    TICK = 1.0     # seconds between status refreshes

    def __init__(self, config: Config, on_status: Callable[[Status], None] = lambda s: None,
                 on_changed: Callable[[], None] = lambda: None, on_chosen: Callable[[object], None] = lambda s: None):
        self.config = config
        self.on_status, self.on_changed, self.on_chosen = on_status, on_changed, on_chosen
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._service: IndexService | None = None
        self._busy_elsewhere = False
        self._error: str | None = None
        self._changed = False
        self._lock = threading.Lock()
        self._lifecycle = threading.Lock()
        self._restart_version = 0
        self.user_paused = False
        self.started = False
        self.status = Status("starting", "Starting…")
        self._closed = threading.Event()
        # The status is refreshed on a timer from the store's own counts, not from every event: cheap, and it can't
        # drift from the truth. The same tick applies the power policy.
        threading.Thread(target=self._ticks, name="indexing status", daemon=True).start()

    # Control

    def start(self) -> None:
        if not self.config.anything:
            self._publish(Status("off", "Nothing chosen to search yet"))
            # A shutdown may have interrupted removal of the last source. Finish without loading a model.
            if self.config.selection.forgotten or self.config.selection.removed:
                self._stop = threading.Event()
                self._thread = threading.Thread(target=self._clean_empty, args=(self._stop, self.config),
                                                name="finish removal", daemon=True)
                self.started = True
                self._thread.start()
            return
        if self.config.choice is None:
            self._publish(Status("off", "Not set up yet"))
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop, self.config), name="indexing",
                                        daemon=True)
        self._thread.start()
        self.started = True

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.stop()
        self._closed.set()

    def configure(self, config: Config) -> None:
        """New choices of what to search go to the running service, which applies just the difference. New hardware
        or new types to skip restart it (its first crawl applies them)."""
        if config == self.config:
            return
        if (self._service is not None and self.started and config.choice == self.config.choice
                and config.skip == self.config.skip and config.model == self.config.model):
            self.config = config
            self._service.set_selection(config.selection)
            return
        old_thread, self.config = self._thread, config
        self.stop()
        self._service = None
        self.started = False

        def restart() -> None:
            if old_thread is not None:
                old_thread.join(30)
            self.start()

        threading.Thread(target=restart, daemon=True).start()

    def quiesce(self, timeout: float = 60) -> None:
        """Stop the writer before changing embedding spaces. Never start another writer after a join timeout."""
        with self._lifecycle:
            self._restart_version += 1   # invalidate a resume queued by a previous failed model change
            self.stop()
            old = self._thread
        if old is not None:
            old.join(timeout)
            if old.is_alive():
                raise Busy("The current file is still being read. Try changing the model again shortly.")
        self._service = None
        self.started = False

    def resume(self) -> None:
        with self._lifecycle:
            self._restart_version += 1
            version, old = self._restart_version, self._thread
        def work() -> None:
            if old is not None:
                old.join()
            with self._lifecycle:
                if not self._closed.is_set() and version == self._restart_version:
                    self.started = False
                    self.start()
        threading.Thread(target=work, daemon=True, name="resume indexing").start()

    def read_again(self, paths: list[str]) -> None:
        if self._service is not None:
            self._service.read_again(paths)

    def rebuild(self, done: Callable[[], None] = lambda: None) -> None:
        """Every file read again: the service stops, the index is emptied in place (readers keep it open), and a
        new service starts from nothing."""
        old_thread = self._thread
        self.stop()
        self._service = None

        def work() -> None:
            if old_thread is not None:
                old_thread.join(60)
            store = Store(default_file())
            try:
                with writer_lock(store):
                    store.clear()
            finally:
                store.close()
            self.start()
            done()

        threading.Thread(target=work, daemon=True, name="rebuild").start()

    def set_paused(self, paused: bool) -> None:
        self.user_paused = paused
        self._refresh()

    # Background

    def _clean_empty(self, stop: threading.Event, config: Config) -> None:
        while not stop.is_set():
            store = None
            try:
                store = Store(default_file())
                with writer_lock(store):
                    if stop.is_set():
                        return
                    Indexer(store, config.options()).sync(config.selection)
                self._busy_elsewhere = False
                self.on_changed()
                return
            except Busy:
                self._busy_elsewhere = True
            except Exception as error:
                self._error = f"{type(error).__name__}: {error}"
                self._publish(Status("error", "Couldn't finish removing search data; retrying. Originals are untouched."))
            finally:
                if store is not None:
                    store.close()
            stop.wait(self.RETRY)

    def _run(self, stop: threading.Event, config: Config) -> None:
        while not stop.is_set():
            store = Store(default_file())
            service = IndexService(store, config.selection, config.options(), model_factory(config.choice, config.model),
                                   on_event=self._on_event)
            self._service = service
            try:
                self._busy_elsewhere = False
                service.run(stop)
            except Busy:
                self._busy_elsewhere = True   # a `dowse watch` or `index` in a terminal: let it, try again later
            except Exception as error:
                self._error = f"{type(error).__name__}: {error}"
            finally:
                store.close()
            stop.wait(self.RETRY)

    def _on_event(self, event: Event) -> None:   # on the service's thread
        if event.kind in ("read", "change", "sync", "chosen"):
            self._changed = True
        if event.kind == "chosen":
            self.on_chosen(event.summary)
        if event.kind == "error":
            self._error = event.message

    # Status and power

    def power_reason(self) -> str | None:
        on_battery, saver = winapi.power()
        if saver:
            return "Battery Saver is on"
        if on_battery and self.config.plugged_in_only:
            return "waiting to be plugged in"
        return None

    def _ticks(self) -> None:
        while not self._closed.wait(self.TICK):
            with contextlib.suppress(Exception):
                self._refresh()

    def _refresh(self) -> None:
        if self._changed:
            self._changed = False
            self.on_changed()
        service = self._service
        if not self.config.anything and service is not None:
            self._publish(Status("off", "Nothing chosen to search yet"))
            return
        if self._busy_elsewhere:
            self._publish(Status("waiting", "Another Dowse is indexing"))
            return
        if service is None:
            return
        reason = self.power_reason()
        paused = self.user_paused or reason is not None
        (service.paused.set if paused else service.paused.clear)()
        try:
            store = Store(default_file())
            done, total = store.progress()
            partial = len(store.partial())
            store.close()
        except Exception:
            return
        if self.user_paused:
            self._publish(Status("paused", "Indexing paused", done, total))
        elif reason:
            self._publish(Status("paused", f"Paused: {reason}", done, total))
        elif not service.ready.is_set():
            self._publish(Status("starting", "Looking through your folders…", done, total))
        elif done < total:
            self._publish(Status("reading", f"Reading {done:,} of {total:,} files", done, total))
        elif partial:
            self._publish(Status("reading", f"Reading the rest of {partial:,} long files", done, total))
        else:
            self._publish(Status("idle", f"Up to date · {total:,} files", done, total))

    def _publish(self, status: Status) -> None:
        with self._lock:
            if status == self.status:
                return
            self.status = status
        self.on_status(status)
