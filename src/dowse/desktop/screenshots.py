"""New screenshots into Dowse. While the switch is on (Settings, `screenshots`), each screenshot Windows saves as a file
(Win+PrtScn; Windows 11's Snipping Tool; Print Screen when OneDrive is set to save screenshots) is added to the
files Dowse searches by name, once it's written. Only screenshots saved from then on: those already there are the
user's to add. Screenshots only copied to the clipboard (Win+Shift+S and Print Screen on Windows 10) make no file
and aren't looked for: the clipboard is never read.

Where: Windows' Screenshots known folder (wherever it was moved to), and OneDrive's Pictures\\Screenshots when there
is one. A folder that isn't there yet (no screenshot saved on this PC so far) is looked for again now and then.
"""

import ctypes
import logging
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable

from ..watcher import Changes, FolderWatcher
from .. import paths, rules

log = logging.getLogger("dowse.screenshots")

FOLDERID_SCREENSHOTS = "b7bede81-df94-4682-a7d8-57a52620b86f"
PICTURES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic")
QUIET = 2.0       # seconds a new file must be still before it's added (as the indexer's watcher waits)
LOOK_AGAIN = 30.0  # seconds between looks for a screenshot folder that isn't there yet


def known_folder(guid: str) -> str | None:
    """A Windows known folder's path (where the user moved it, if they did); None elsewhere or if it can't be had."""
    if sys.platform != "win32":
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    raw = uuid.UUID(guid).bytes_le
    wanted = GUID.from_buffer_copy(raw)
    found = ctypes.c_wchar_p()
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    try:
        if shell32.SHGetKnownFolderPath(ctypes.byref(wanted), 0, None, ctypes.byref(found)) != 0:
            return None
        return found.value
    finally:
        ole32.CoTaskMemFree(found)


def folders() -> list[str]:
    """Where Windows (and OneDrive) save screenshots on this PC; on a Mac, where macOS saves them (the Desktop unless
    another folder was chosen)."""
    if sys.platform == "darwin":
        from . import macapi
        return [os.path.normpath(macapi.screenshots_folder())]
    out = []
    windows = known_folder(FOLDERID_SCREENSHOTS) or os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")
    out.append(windows)
    onedrive = os.environ.get("ONEDRIVE")   # (Windows: any case)
    if onedrive:
        out.append(os.path.join(onedrive, "Pictures", "Screenshots"))
    return list(dict.fromkeys(os.path.normpath(f) for f in out))


class ScreenshotWatcher:
    """Calls `on_new` with each batch of screenshots saved in `roots` since `start` (settled: whole and openable)."""

    def __init__(self, on_new: Callable[[list[str]], None], roots: list[str] | None = None, quiet: float | None = None):
        self.on_new = on_new
        self.roots = roots if roots is not None else folders()
        self.changes = Changes(quiet=QUIET if quiet is None else quiet)
        self._watcher: FolderWatcher | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._since = 0.0
        self._seen: set[str] = set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._since = time.time()
        self._stop.clear()
        self._watcher = FolderWatcher(self.changes)
        self._watch()
        self._thread = threading.Thread(target=self._run, name="screenshots", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    def _watch(self) -> None:
        """The screenshot folders there now, each by itself (not what's in its subfolders)."""
        if self._watcher is not None:
            self._watcher.watch([], singles=[r for r in self.roots if os.path.isdir(r)])

    def _run(self) -> None:
        looked = time.monotonic()
        while not self._stop.wait(0.5):
            if time.monotonic() - looked >= LOOK_AGAIN:   # a folder made since (the first Win+PrtScn on this PC)
                looked = time.monotonic()
                try:
                    self._watch()
                except OSError:
                    log.warning("watching the screenshot folders", exc_info=True)
            if not self.changes.ready():
                continue
            batch = self.changes.take()
            fresh = [p for p in [*batch.paths, *(new for _, new in batch.renames)] if self._new(p)]
            if fresh:
                self._seen.update(paths.fold(p) for p in fresh)
                try:
                    self.on_new(fresh)
                except Exception:
                    log.exception("adding screenshots")

    def _new(self, path: str) -> bool:
        """A picture made since the switch went on, in a screenshot folder itself, not seen before."""
        if not path.lower().endswith(PICTURES) or paths.fold(path) in self._seen:
            return False
        if paths.fold(os.path.dirname(path)) not in {paths.fold(r) for r in self.roots}:
            return False
        if sys.platform == "darwin" and not rules.is_screenshot(path):
            return False   # a Mac saves screenshots beside other files (the Desktop): only those named as screenshots
        try:
            info = os.stat(path)
        except OSError:
            return False
        made = getattr(info, "st_birthtime", info.st_ctime)   # when the file was created (Windows: st_ctime)
        return os.path.isfile(path) and made >= self._since - 1
