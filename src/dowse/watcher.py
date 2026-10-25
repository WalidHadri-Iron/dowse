"""Folder changes, from the system's own record of a folder tree (Windows: ReadDirectoryChangesW; macOS: FSEvents;
Linux: inotify), settled into batches.

One thread per chosen folder blocks in ReadDirectoryChangesW and records what changed in `Changes`; nothing else
happens on those threads. A path is handed over once it has been quiet for a moment, its size has stopped moving and
it can be opened (a download being written, a document Word holds open), so the indexer never reads half a file.

Why not the `watchdog` package: on Windows it drops the one signal that matters most here. When changes come faster
than the buffer holds (unzipping thousands of files), Windows says so with an empty read, and watchdog turns that into
no events at all. Here it's `Batch.overflowed`, and the service crawls that folder again. On a Mac, FSEvents says the
same with "must scan subfolders" or "events dropped", and Linux's inotify with a queue overflow: the same thing happens.
"""

import ctypes
import os
import select
import struct
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, field
from . import paths

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


# macOS: FSEvents (CoreServices), through ctypes as Windows' API is. One stream per folder, on its own thread's run loop.
FS_NO_DEFER, FS_WATCH_ROOT, FS_FILE_EVENTS = 0x02, 0x04, 0x10                 # kFSEventStreamCreateFlag…
FS_MUST_SCAN, FS_USER_DROPPED, FS_KERNEL_DROPPED = 0x01, 0x02, 0x04          # kFSEventStreamEventFlag…
FS_ROOT_CHANGED, FS_UNMOUNT = 0x20, 0x80
FS_CREATED, FS_REMOVED, FS_INODE_META, FS_RENAMED, FS_MODIFIED = 0x100, 0x200, 0x400, 0x800, 0x1000
FS_IS_DIR = 0x20000
FS_SINCE_NOW = 0xFFFFFFFFFFFFFFFF
FS_LATENCY = 0.3   # seconds FSEvents gathers events before calling: they're settled here anyway (`Changes`)

if sys.platform == "darwin":
    _cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    _cs = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
    _FSCALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint64))
    _cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    _cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    _cf.CFArrayCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, ctypes.c_void_p]
    _cf.CFArrayCreate.restype = ctypes.c_void_p
    _cf.CFRelease.argtypes = [ctypes.c_void_p]
    _cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
    _cf.CFRunLoopRun.restype = None
    _cf.CFRunLoopStop.argtypes = [ctypes.c_void_p]
    _cf.CFRunLoopWakeUp.argtypes = [ctypes.c_void_p]
    _cs.FSEventStreamCreate.argtypes = [ctypes.c_void_p, _FSCALLBACK, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_uint64, ctypes.c_double, ctypes.c_uint32]
    _cs.FSEventStreamCreate.restype = ctypes.c_void_p
    _cs.FSEventStreamScheduleWithRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    for _name in ("FSEventStreamStop", "FSEventStreamInvalidate", "FSEventStreamRelease"):
        getattr(_cs, _name).argtypes = [ctypes.c_void_p]
    _cs.FSEventStreamStart.argtypes = [ctypes.c_void_p]
    _cs.FSEventStreamStart.restype = ctypes.c_bool
    _DEFAULT_MODE = ctypes.c_void_p.in_dll(_cf, "kCFRunLoopDefaultMode").value


class _MacWatch(threading.Thread):
    """One folder, watched with FSEvents until `stop` (the same contract as `_Watch`). FSEvents names the real path
    (/private/var/… for /var/…): it's given back under the folder as it was chosen, which is how the index knows it.
    A rename arrives as two events, the old name then the new: paired back into one."""

    def __init__(self, root: str, changes: Changes, recursive: bool = True):
        super().__init__(name=f"watch {root}", daemon=True)
        self.root, self.changes, self.recursive = root, changes, recursive
        self.real = os.path.realpath(root)
        self.stopping = False
        self.started_ok = threading.Event()
        self._loop = None
        self._callback = _FSCALLBACK(self._events)   # kept: FSEvents calls it for the stream's whole life

    def _chosen(self, path: str) -> str:
        """`path` under the folder as it was chosen."""
        real = self.real.rstrip("/")
        if path == real or path.startswith(real + "/"):
            return self.root.rstrip("/") + path[len(real):]
        return path

    def run(self) -> None:
        name = _cf.CFStringCreateWithCString(None, self.real.encode("utf-8"), 0x08000100)   # kCFStringEncodingUTF8
        paths = _cf.CFArrayCreate(None, (ctypes.c_void_p * 1)(name), 1, None)
        stream = _cs.FSEventStreamCreate(None, self._callback, None, paths, FS_SINCE_NOW, FS_LATENCY,
                                         FS_NO_DEFER | FS_WATCH_ROOT | FS_FILE_EVENTS)
        _cf.CFRelease(paths)
        _cf.CFRelease(name)
        if not stream or not os.path.isdir(self.root):
            self.changes.lose(self.root)
            self.started_ok.set()
            return
        self._loop = _cf.CFRunLoopGetCurrent()
        _cs.FSEventStreamScheduleWithRunLoop(stream, self._loop, _DEFAULT_MODE)
        if not _cs.FSEventStreamStart(stream):
            self.changes.lose(self.root)
        else:
            self.started_ok.set()
            if not self.stopping:
                _cf.CFRunLoopRun()   # returns when `stop` (or the folder going away) stops the loop
            _cs.FSEventStreamStop(stream)
        _cs.FSEventStreamInvalidate(stream)
        _cs.FSEventStreamRelease(stream)
        self.started_ok.set()

    def _events(self, _stream, _info, count, paths, flags, ids) -> None:
        names = ctypes.cast(paths, ctypes.POINTER(ctypes.c_char_p))
        events = [(os.fsdecode(names[i]), flags[i], ids[i]) for i in range(count)]
        renamed_from = None
        for path, flag, event_id in events:
            path = self._chosen(path)
            if flag & (FS_MUST_SCAN | FS_USER_DROPPED | FS_KERNEL_DROPPED):
                self.changes.overflow(self.root)   # FSEvents lost track below here: crawl again
                continue
            if flag & (FS_ROOT_CHANGED | FS_UNMOUNT) and not os.path.isdir(self.root):
                self.changes.lose(self.root)       # the folder went away: deleted, moved, unplugged
                self.stop()
                return
            if not self.recursive and os.path.dirname(path.rstrip("/")) != self.root.rstrip("/"):
                continue                           # a folder of named files: not what's under it
            if flag & FS_RENAMED:
                if renamed_from and renamed_from[1] + 1 == event_id and not os.path.exists(renamed_from[0]):
                    self.changes.rename(renamed_from[0], path)
                    renamed_from = None
                else:
                    if renamed_from:
                        self.changes.touch(renamed_from[0])
                    renamed_from = (path, event_id)
                continue
            if renamed_from:
                self.changes.touch(renamed_from[0])   # a rename with no partner: moved in from, or out to, elsewhere
                renamed_from = None
            if flag & FS_IS_DIR and not flag & (FS_CREATED | FS_REMOVED):
                continue                           # a folder "changes" when something in it does, which reports itself
            if flag & (FS_CREATED | FS_REMOVED | FS_MODIFIED | FS_INODE_META):
                self.changes.touch(path)
        if renamed_from:
            self.changes.touch(renamed_from[0])

    def stop(self) -> None:
        self.stopping = True
        if self._loop is not None:
            _cf.CFRunLoopStop(self._loop)
            _cf.CFRunLoopWakeUp(self._loop)


if sys.platform.startswith("linux"):
    _libc = ctypes.CDLL(None, use_errno=True)
    _libc.inotify_init1.argtypes = [ctypes.c_int]
    _libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    _libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]

IN_MODIFY, IN_ATTRIB, IN_CLOSE_WRITE = 0x2, 0x4, 0x8
IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x40, 0x80, 0x100, 0x200
IN_DELETE_SELF, IN_MOVE_SELF, IN_UNMOUNT, IN_Q_OVERFLOW, IN_IGNORED = 0x400, 0x800, 0x2000, 0x4000, 0x8000
IN_ONLYDIR, IN_ISDIR, IN_NONBLOCK, IN_CLOEXEC = 0x01000000, 0x40000000, 0o4000, 0o2000000
IN_EVENTS = (IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
             | IN_DELETE_SELF | IN_MOVE_SELF | IN_ONLYDIR)
_EVENT = struct.Struct("iIII")   # struct inotify_event: wd, mask, cookie, len (then the name, padded)


class _LinuxWatch(threading.Thread):
    """One folder, watched with inotify until `stop` (the same contract as `_Watch`). inotify watches a folder, not a
    tree: each folder below gets its own watch, those the crawl would skip aside (node_modules, hidden folders: each
    watch is kernel memory, and the system allows so many), and folders that appear are watched as they do. A rename
    is two events with one cookie, paired back into one. Past the system's limit on watches (fs.inotify.
    max_user_watches), what's left unwatched is found by the regular full check."""

    def __init__(self, root: str, changes: Changes, recursive: bool = True):
        super().__init__(name=f"watch {root}", daemon=True)
        self.root, self.changes, self.recursive = root.rstrip("/") or "/", changes, recursive
        self.started_ok = threading.Event()
        self.stopping = False
        self._fd = -1
        self._wake_read, self._wake_write = os.pipe()
        self._folders: dict[int, str] = {}   # watch descriptor → folder
        self._full = False                   # the system's limit was reached

    def _add(self, folder: str, below: bool) -> None:
        """Watches `folder`, and with `below` every folder under it the crawl would walk into."""
        from . import rules

        pending = [folder]
        while pending:
            current = pending.pop()
            wd = _libc.inotify_add_watch(self._fd, os.fsencode(current), IN_EVENTS)
            if wd < 0:
                if ctypes.get_errno() == 28 and not self._full:   # ENOSPC: max_user_watches reached
                    self._full = True
                continue
            self._folders[wd] = current
            if not (below and self.recursive):
                continue
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False) and \
                                not rules.folder_skip_reason(entry.path, rules.attributes(entry.path), []):
                            pending.append(entry.path)
            except OSError:
                pass

    def _moved(self, old: str, new: str) -> None:
        """A watched folder renamed within the tree: its watches stay, under the new name."""
        for wd, folder in self._folders.items():
            if folder == old or folder.startswith(old + "/"):
                self._folders[wd] = new + folder[len(old):]

    def run(self) -> None:
        self._fd = _libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self._fd < 0 or not os.path.isdir(self.root):
            self.changes.lose(self.root)
            self.started_ok.set()
            return
        try:
            self._add(self.root, below=True)
            if not self._folders:
                self.changes.lose(self.root)
                return
            self.started_ok.set()
            while not self.stopping:
                ready, _, _ = select.select([self._fd, self._wake_read], [], [])
                if self.stopping or self._fd not in ready:
                    break
                try:
                    data = os.read(self._fd, 1 << 16)
                except BlockingIOError:
                    continue
                if not self._record(data):
                    break
        finally:
            self.started_ok.set()
            os.close(self._fd)
            os.close(self._wake_read)
            os.close(self._wake_write)

    def _record(self, data: bytes) -> bool:
        """Records one read's events; False when the folder itself is gone."""
        moved_from: dict[int, tuple[str, bool]] = {}   # cookie → (old path, a folder)
        offset = 0
        while offset < len(data):
            wd, mask, cookie, length = _EVENT.unpack_from(data, offset)
            name = os.fsdecode(data[offset + _EVENT.size:offset + _EVENT.size + length].rstrip(b"\0"))
            offset += _EVENT.size + length
            if mask & IN_Q_OVERFLOW:
                self.changes.overflow(self.root)   # the kernel's queue filled up: crawl again
                continue
            folder = self._folders.get(wd)
            if folder is None:
                continue
            if mask & IN_IGNORED:
                del self._folders[wd]
                if folder == self.root:
                    self.changes.lose(self.root)   # the folder went away: deleted, moved, unplugged
                    return False
                continue
            if mask & (IN_DELETE_SELF | IN_MOVE_SELF | IN_UNMOUNT):
                if folder == self.root and (mask & IN_UNMOUNT or not os.path.isdir(self.root)):
                    self.changes.lose(self.root)
                    return False
                continue   # a folder below: its parent reports it
            path = os.path.join(folder, name)
            directory = bool(mask & IN_ISDIR)
            if mask & IN_MOVED_FROM:
                moved_from[cookie] = (path, directory)
                continue
            if mask & IN_MOVED_TO:
                if cookie in moved_from:
                    old, _ = moved_from.pop(cookie)
                    if directory:
                        self._moved(old, path)
                    self.changes.rename(old, path)
                else:   # moved in from outside the tree
                    if directory:
                        self._add(path, below=True)
                    self.changes.touch(path)
                continue
            if directory and mask & IN_CREATE:
                self._add(path, below=True)        # and what was put in it before its watch began, by a crawl:
                self.changes.touch(path)
                continue
            if directory and not mask & IN_DELETE:
                continue                           # a folder "changes" when something in it does, which reports itself
            self.changes.touch(path)
        for old, directory in moved_from.values():  # moved out of the tree: gone from here
            if directory:
                for wd in [w for w, f in self._folders.items() if f == old or f.startswith(old + "/")]:
                    _libc.inotify_rm_watch(self._fd, wd)
            self.changes.touch(old)
        return True

    def stop(self) -> None:
        self.stopping = True
        try:
            os.write(self._wake_write, b"x")
        except OSError:
            pass


class FolderWatcher:
    """Watches folder trees, and single folders (where files added by name live), and collects their changes in
    `changes`. Folders inside another one watched are covered by it. Windows, macOS and Linux."""

    MAX_SINGLES = 64   # folders of named files watched one by one at most; beyond, the regular full check finds changes

    def __init__(self, changes: Changes):
        if sys.platform not in ("win32", "darwin") and not sys.platform.startswith("linux"):
            raise OSError("watching folders needs Windows, macOS or Linux")
        self.changes = changes
        self._watches: dict[str, _Watch] = {}

    @staticmethod
    def outermost(roots: list[str]) -> list[str]:
        roots = sorted({os.path.abspath(r) for r in roots}, key=lambda r: len(r))
        out: list[str] = []
        for root in roots:
            if not any(paths.fold(root).startswith(paths.fold(o).rstrip("\\/") + os.sep) for o in out):
                out.append(root)
        return out

    def watch(self, roots: list[str], singles: list[str] = ()) -> None:
        """Watches `roots` with all they hold, and `singles` (folders, not what's under them) unless a root covers
        them; only those there now (the others stay on `missing`). Stops watching any others."""
        trees = [r for r in self.outermost(roots) if os.path.isdir(r)]
        covered = [paths.fold(r).rstrip("\\/") for r in trees]
        lone = sorted({os.path.abspath(s) for s in singles if os.path.isdir(s) and not any(
            paths.fold(os.path.abspath(s)) == c or paths.fold(os.path.abspath(s)).startswith(c + os.sep)
            for c in covered)})[:self.MAX_SINGLES]
        wanted = {r: True for r in trees} | {s: False for s in lone}
        for root in list(self._watches):
            if root not in wanted or self._watches[root].recursive != wanted[root] or not self._watches[root].is_alive():
                self._watches.pop(root).stop()
        for root, recursive in wanted.items():
            if root not in self._watches:
                kind = _MacWatch if sys.platform == "darwin" else _Watch if sys.platform == "win32" else _LinuxWatch
                thread = kind(root, self.changes, recursive)
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
