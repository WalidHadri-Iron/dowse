"""Getting a PC ready to search: the llama.cpp build for its hardware and the model, downloaded on a worker thread
with progress (stage, bytes, speed, time left), then a few seconds measuring how fast this PC reads files (`speed`).
Also the usual folders to suggest, counted in the background, with how long reading them will take here.

Setup survives being interrupted: downloads resume where they stopped (closing the app halfway loses nothing), a
dropped connection is retried, a model file that's here but damaged is fetched again, and nothing is recorded as set
up (`runtime.json`) before the model has run on this PC. On first setup, a graphics card that can't run the model
(an old or broken driver) leaves Dowse on the processor instead of failing."""

import contextlib
import logging
import os
import threading
import time

import httpx
import numpy as np
from PIL import Image
from collections.abc import Callable

from .. import crawler, modelfiles, paths, runtime, speed
from . import winapi
from ..reading import Options
from ..embedder import Embedder, Prompts
from ..rules import Kind
from ..selection import Selection

log = logging.getLogger("dowse.setup")

RUNTIME_BYTES = 32 << 20   # about what the Vulkan or CPU build weighs (its size isn't known before it's fetched)
SUGGESTED = ["Pictures", "Documents", "Downloads", "Desktop", "Videos", "Music"]


def needed() -> bool:
    choice = runtime.load()
    return choice is None or not runtime.installed(choice.backend) or bool(modelfiles.missing())


class SetupJob:
    """Downloads what `backend` needs; `on_progress(dict)`, then `on_done(choice)` or `on_failed(why)`."""

    def __init__(self, backend: str, on_progress: Callable[[dict], None], on_done: Callable[[runtime.Choice], None],
                 on_failed: Callable[[str], None], *, model: str | modelfiles.Model | None = None,
                 choice: runtime.Choice | None = None, fallback: bool = False):
        self.backend = backend
        self.fallback = fallback   # a GPU that fails validation: the processor instead (first setup, not a switch)
        self.model = modelfiles.get(model)
        self.choice = choice
        self.cancelled = threading.Event()
        self.activating = False
        self._mutex = threading.Lock()
        self._checking: runtime.Server | None = None
        self.on_progress, self.on_done, self.on_failed = on_progress, on_done, on_failed
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="setup", daemon=True)
            self.thread.start()

    def cancel(self) -> bool:
        with self._mutex:
            if self.activating or not self.thread or not self.thread.is_alive():
                return False
            self.cancelled.set()
            server = self._checking
        if server:
            threading.Thread(target=server.stop, daemon=True).start()
        return True

    def _check(self) -> None:
        if self.cancelled.is_set():
            raise InterruptedError("Model preparation cancelled")

    def _retrying(self, work, tries: int = 3):
        """`work`, tried again after a dropped connection (a download resumes where it stopped)."""
        for attempt in range(tries):
            self._check()
            try:
                return work()
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                if attempt == tries - 1 or (isinstance(error, httpx.HTTPStatusError)
                                            and error.response.status_code < 500):
                    raise
                log.warning("download interrupted (%s): trying again", error)
                time.sleep(2 * (attempt + 1))

    def _validate(self, choice: runtime.Choice) -> None:
        """Smoke-test the actual runtime and all advertised modalities before any index/configuration change."""
        modelfiles.verify(self.model)
        self._check()
        server = runtime.Server(choice, model=self.model, log=paths.logs() / "server-model-check.log", fallback=False)
        with self._mutex:
            self._checking = server
        try:
            server.start(cancel=self.cancelled.is_set)
            with Embedder(server.url, parallel=1, model=self.model) as embedder:
                vectors = [embedder.query("Find the sample document"),
                           embedder.texts([Prompts.document("The sample document is about a mountain walk.")])[0]]
                if self.model.media:
                    vectors += embedder.images([Image.new("RGB", (64, 48), "white")], 70)
                    vectors += embedder.audio([speed._sound()])
                if any(not isinstance(v, np.ndarray) or not np.isfinite(v).all() or np.linalg.norm(v) < 0.1 for v in vectors):
                    raise RuntimeError("The model could not produce valid embeddings for its supported file types")
        finally:
            server.stop()
            with self._mutex:
                self._checking = None

    def _run(self) -> None:
        try:
            self._check()
            model = sum(f.size for f in modelfiles.missing(model=self.model))
            ready = runtime.load()
            total = model + (0 if ready is not None and runtime.installed(ready.backend) else RUNTIME_BYTES)
            started, before = time.monotonic(), [0]

            def report(stage: str):
                def callback(done: int, _total: int) -> None:
                    self._check()
                    so_far = before[0] + done
                    rate = so_far / max(time.monotonic() - started, 0.001)
                    left = (max(total, so_far) - so_far) / rate if rate > 0 else None
                    self.on_progress({"stage": stage, "done": so_far, "total": max(total, so_far), "rate": rate,
                                      "left": left})
                return callback

            choice = self.choice or self._retrying(lambda: runtime.choose(self.backend, progress=report("runtime")))
            before[0] = total - model
            for file in modelfiles.missing(model=self.model):
                self._retrying(lambda f=file: modelfiles.fetch_one(f, progress=report("model")))
                before[0] += file.size
            self._check()
            self.on_progress({"stage": "validate", "done": total, "total": total, "rate": 0, "left": None})
            for file in modelfiles.damaged(self.model):   # here, but not the right bytes: fetched again
                log.warning("%s is damaged: downloading it again", file.name)
                modelfiles.path(file).unlink()
                self._retrying(lambda f=file: modelfiles.fetch_one(f, progress=report("model")))
            try:
                self._validate(choice)
            except (OSError, TimeoutError, RuntimeError) as error:
                if not (self.fallback and choice.device):
                    raise
                log.warning("%s can't run the model (%s): using the processor", choice.name or choice.describe(), error)
                self._check()
                choice = self._retrying(lambda: runtime.choose("cpu", progress=report("runtime")))
                self._validate(choice)
            self._check()
            if not speed.load(choice, choice.device is not None, model=self.model).measured:
                self.on_progress({"stage": "speed", "done": total, "total": total, "rate": 0, "left": None})
                with contextlib.suppress(Exception):   # if not, the estimate stays a guess: nothing else needs it
                    speed.measure(choice, model=self.model)
            self._check()
            with self._mutex:
                self._check()
                self.activating = True
            self.on_progress({"stage": "activate", "done": total, "total": total, "rate": 0, "left": None})
            self.on_done(choice)
            self.on_progress({"stage": "done", "done": total, "total": total, "rate": 0, "left": None})
        except Exception as error:   # no network, a full disk: said plainly, and setup can be tried again
            self.on_failed(f"{type(error).__name__}: {error}")


def suggestions() -> list[dict]:
    """The usual folders that exist here: [{"name", "path"}]."""
    out = []
    for name in SUGGESTED:
        path = winapi.known_folder(name)
        if os.path.isdir(path):
            out.append({"name": name, "path": path})
    return out


class Counter:
    """Counts what folders hold, one worker thread per folder; `on_counted(folder, {kind: n})`."""

    def __init__(self, on_counted: Callable[[str, dict[str, int]], None]):
        self.on_counted = on_counted
        self._done: dict[str, dict[str, int] | None] = {}
        self._lock = threading.Lock()

    def count(self, folder: str) -> dict[str, int] | None:
        with self._lock:
            if folder in self._done:
                return self._done[folder]
            self._done[folder] = None
        threading.Thread(target=self._work, args=(folder,), daemon=True, name="count").start()
        return None

    def _work(self, folder: str) -> None:
        counts: dict[str, int] = {}
        for found in crawler.crawl(Selection((folder,))).files:
            counts[found.kind.value] = counts.get(found.kind.value, 0) + 1
        with self._lock:
            self._done[folder] = counts
        self.on_counted(folder, counts)


def estimate(counts: dict[str, int], choice: runtime.Choice | None, gpu: bool, model: str | modelfiles.Model | None = None) -> dict:
    """{"seconds", "measured"}: how long reading these files takes on this PC."""
    measured = speed.load(choice, gpu, model=model)
    seconds = speed.seconds({Kind(k): n for k, n in counts.items()}, measured, Options.for_device(gpu))
    return {"seconds": seconds, "measured": measured.measured}
