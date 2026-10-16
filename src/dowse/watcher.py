"""Folder changes, from Windows' own change journal for a folder tree (ReadDirectoryChangesW), settled into batches.

One thread per chosen folder blocks in ReadDirectoryChangesW and records what changed in `Changes`; nothing else
happens on those threads. A path is handed over once it has been quiet for a moment, its size has stopped moving and
it can be opened (a download being written, a document Word holds open), so the indexer never reads half a file.

Why not the `watchdog` package: on Windows it drops the one signal that matters most here. When changes come faster
than the buffer holds (unzipping thousands of files), Windows says so with an empty read, and watchdog turns that into
no events at all. Here it's `Batch.overflowed`, and the service crawls that folder again.
"""

import ctypes
import os
import struct
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, field

FILE_LIST_DIRECTORY = 0x0001
SHARE_ALL = 0x1 | 0x2 | 0x4                       # read, write, delete: never in anyone's way
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000           # needed to open a folder
CHANGES = 0x1 | 0x2 | 0x4 | 0x8 | 0x10            # file name, folder name, attributes (OneDrive), size, last write
ADDED, REMOVED, MODIFIED, RENAMED_OLD, RENAMED_NEW = 1, 2, 3, 4, 5
ERROR_OPERATION_ABORTED, ERROR_INVALID_PARAMETER = 995, 87
INVALID_HANDLE = ctypes.c_void_p(-1).value
BIG_BUFFER, NETWORK_BUFFER = 1 << 20, 64 * 1024   # shares refuse buffers over 64 KB


@dataclass
class Batch:
    paths: list[str] = field(default_factory=list)                # created, changed or deleted: files or folders
    renames: list[tuple[str, str]] = field(default_factory=list)  # (old, new)
    overflowed: set[str] = field(default_factory=set)             # roots whose changes were lost: crawl again
    lost: set[str] = field(default_factory=set)                   # roots that went away (unplugged, deleted)

    def __bool__(self) -> bool:
        return bool(self.paths or self.renames or self.overflowed or self.lost)


@dataclass
class _Seen:
    when: float
    size: int | None = None
    mtime: float | None = None


class Changes:
    """What the watch threads saw, until it settles. Thread-safe."""

    def __init__(self, quiet: float = 2.0, clock: Callable[[], float] = time.monotonic):
        self.quiet, self.clock = quiet, clock
        self._lock = threading.Lock()
        self._paths: dict[str, _Seen] = {}
        self._renames: list[tuple[str, str]] = []
        self._overflowed: set[str] = set()
        self._lost: set[str] = set()

    def touch(self, path: str) -> None:
        size, mtime = _stat(path)   # what it looks like now: if it still does after a quiet spell, it's settled
        with self._lock:
            self._paths[path] = _Seen(self.clock(), size, mtime)

    def rename(self, old: str, new: str) -> None:
        size, mtime = _stat(new)
        with self._lock:
            self._renames.append((old, new))
            self._paths.pop(old, None)
            self._paths[new] = _Seen(self.clock(), size, mtime)

    def overflow(self, root: str) -> None:
        with self._lock:
            self._overflowed.add(root)

    def lose(self, root: str) -> None:
        with self._lock:
            self._lost.add(root)

    def ready(self) -> bool:
        """Is anything settled (or urgent) waiting? Cheap: for asking between steps of a long file."""
        now = self.clock()
        with self._lock:
            return bool(self._overflowed or self._lost or
                        any(now - seen.when >= self.quiet for seen in self._paths.values()))

    def waiting(self) -> int:
        with self._lock:
            return len(self._paths)

    def take(self) -> Batch:
        """What has settled: quiet for `quiet` seconds, the same size and time as at its last change, and openable.
        A path still moving (or held open) is looked at again after another quiet spell. Renames go with their new
        path."""
        now = self.clock()
        with self._lock:
            due = {p: s for p, s in self._paths.items() if now - s.when >= self.quiet}
        settled = []
        for path, seen in due.items():
            try:
                info = os.stat(path)
            except OSError:
                settled.append(path)   # gone (or never a file we can look at): the indexer decides
                continue
            if os.path.isdir(path):
                settled.append(path)
                continue
            if (seen.size, seen.mtime) != (info.st_size, info.st_mtime) or not _openable(path):
                with self._lock:   # still being written (or held): look again after another quiet spell
                    if path in self._paths:
                        self._paths[path] = _Seen(self.clock(), info.st_size, info.st_mtime)
                continue
            settled.append(path)
        with self._lock:
            for path in settled:
                self._paths.pop(path, None)
            done = set(settled)
            renames = [r for r in self._renames if r[1] in done or r[1] not in self._paths]
            self._renames = [r for r in self._renames if r not in renames]
            batch = Batch([p for p in settled if p not in {new for _, new in renames}], renames,
                          self._overflowed, self._lost)
            self._overflowed, self._lost = set(), set()
        return batch


def _stat(path: str) -> tuple[int | None, float | None]:
    try:
        info = os.stat(path)
        return info.st_size, info.st_mtime
    except OSError:
        return None, None


def _openable(path: str) -> bool:
    try:
        with open(path, "rb"):
            return True
    except PermissionError:   # a sharing violation: someone holds it open for writing
        return False
    except OSError:
        return True           # gone, or a kind of file open() can't take: not ours to wait for


if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.ReadDirectoryChangesW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.BOOL,
                                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
                                                wintypes.LPVOID]
    _kernel32.ReadDirectoryChangesW.restype = wintypes.BOOL
    _kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetLongPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _kernel32.GetLongPathNameW.restype = wintypes.DWORD


def _long_name(path: str) -> str:
    """Windows sometimes reports a change under the 8.3 short name (PROGRA~1): the real name, when it still exists."""
    if "~" not in path:
        return path
    buffer = ctypes.create_unicode_buffer(32768)
    return buffer.value if _kernel32.GetLongPathNameW(path, buffer, len(buffer)) else path


class _Watch(threading.Thread):
    """One folder, watched until `stop`: with everything under it (`recursive`), or just its own files (the folder
    of a file added by name)."""

    def __init__(self, root: str, changes: Changes, recursive: bool = True):
        super().__init__(name=f"watch {root}", daemon=True)
        self.root, self.changes, self.recursive = root, changes, recursive
        self.handle = None
        self.stopping = False
        self.started_ok = threading.Event()

    def run(self) -> None:
        handle = _kernel32.CreateFileW(self.root, FILE_LIST_DIRECTORY, SHARE_ALL, None, OPEN_EXISTING,
                                       FILE_FLAG_BACKUP_SEMANTICS, None)
        if handle in (None, INVALID_HANDLE):
            self.changes.lose(self.root)
            self.started_ok.set()
            return
        self.handle = handle
        size = BIG_BUFFER
        buffer = ctypes.create_string_buffer(size)
        returned = wintypes.DWORD()
        self.started_ok.set()
        try:
            while not self.stopping:
                ok = _kernel32.ReadDirectoryChangesW(handle, buffer, size, self.recursive, CHANGES,
                                                     ctypes.byref(returned), None, None)
                if not ok:
                    error = ctypes.get_last_error()
                    if self.stopping or error == ERROR_OPERATION_ABORTED:
                        return
                    if error == ERROR_INVALID_PARAMETER and size > NETWORK_BUFFER:   # a network share
                        size = NETWORK_BUFFER
                        buffer = ctypes.create_string_buffer(size)
                        continue
                    self.changes.lose(self.root)   # the folder went away: deleted, unplugged, share offline
                    return
                if returned.value == 0:
                    self.changes.overflow(self.root)   # changes came faster than the buffer held: they're lost
                    continue
                self._record(buffer.raw[:returned.value])
        finally:
            if not self.stopping:
                _kernel32.CloseHandle(handle)
                self.handle = None

    def _record(self, data: bytes) -> None:
        offset, old = 0, None
        while True:
            following, action, length = struct.unpack_from("<III", data, offset)
            name = data[offset + 12:offset + 12 + length].decode("utf-16-le", errors="replace")
            path = _long_name(os.path.join(self.root, name))
            if action == RENAMED_OLD:
                old = path
            elif action == RENAMED_NEW and old is not None:
                self.changes.rename(old, path)
                old = None
            elif action == MODIFIED and os.path.isdir(path):
                pass   # a folder "changes" when something in it does, and that something reports itself
            else:
                self.changes.touch(path)   # added (a folder may come with its contents), removed, or modified
            if not following:
                return
            offset += following

    def stop(self) -> None:
        self.stopping = True
        if self.handle is not None:
            _kernel32.CancelIoEx(self.handle, None)   # wakes the blocked read
            _kernel32.CloseHandle(self.handle)
            self.handle = None


class FolderWatcher:
    """Watches folder trees, and single folders (where files added by name live), and collects their changes in
    `changes`. Folders inside another one watched are covered by it. Windows only."""

    MAX_SINGLES = 64   # folders of named files watched one by one at most; beyond, the regular full check finds changes

    def __init__(self, changes: Changes):
        if sys.platform != "win32":
            raise OSError("watching folders needs Windows")
        self.changes = changes
        self._watches: dict[str, _Watch] = {}

    @staticmethod
    def outermost(roots: list[str]) -> list[str]:
        roots = sorted({os.path.abspath(r) for r in roots}, key=lambda r: len(r))
        out: list[str] = []
        for root in roots:
            if not any(os.path.normcase(root).startswith(os.path.normcase(o).rstrip("\\/") + os.sep) for o in out):
                out.append(root)
        return out

    def watch(self, roots: list[str], singles: list[str] = ()) -> None:
        """Watches `roots` with all they hold, and `singles` (folders, not what's under them) unless a root covers
        them; only those there now (the others stay on `missing`). Stops watching any others."""
        trees = [r for r in self.outermost(roots) if os.path.isdir(r)]
        covered = [os.path.normcase(r).rstrip("\\/") for r in trees]
        lone = sorted({os.path.abspath(s) for s in singles if os.path.isdir(s) and not any(
            os.path.normcase(os.path.abspath(s)) == c or os.path.normcase(os.path.abspath(s)).startswith(c + os.sep)
            for c in covered)})[:self.MAX_SINGLES]
        wanted = {r: True for r in trees} | {s: False for s in lone}
        for root in list(self._watches):
            if root not in wanted or self._watches[root].recursive != wanted[root] or not self._watches[root].is_alive():
                self._watches.pop(root).stop()
        for root, recursive in wanted.items():
            if root not in self._watches:
                thread = _Watch(root, self.changes, recursive)
                thread.start()
                thread.started_ok.wait(5)
                self._watches[root] = thread

    def missing(self, roots: list[str]) -> list[str]:
        """Roots that aren't being watched: not there, or their watch ended (the folder went away)."""
        return [r for r in self.outermost(roots) if r not in self._watches or not self._watches[r].is_alive()]

    def stop(self) -> None:
        for thread in self._watches.values():
            thread.stop()
        for thread in self._watches.values():
            thread.join(5)
        self._watches.clear()
