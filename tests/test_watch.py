"""Live updates: the watcher (on real folders, with Windows' own change notifications), the incremental reconcile,
and the service end to end with a stand-in model."""

import ctypes
import os
import sys
import threading
import time

import pytest
from PIL import Image

from dowse import watcher
from dowse.indexer import Indexer
from dowse.selection import Selection
from dowse.service import IndexService
from dowse.store import Busy, Store, writer_lock
from dowse.watcher import Changes, FolderWatcher
from test_index import FakeEmbedder

windows = pytest.mark.skipif(sys.platform != "win32", reason="watching folders needs Windows")


def photo(path, shade: int = 7) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), (shade, 0, 0)).save(path)


def wait_for(condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


# Settling

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_path_settles_after_a_quiet_spell(tmp_path):
    clock, file = Clock(), tmp_path / "a.txt"
    changes = Changes(quiet=2, clock=clock)
    file.write_text("x")
    changes.touch(str(file))
    clock.now = 1.5
    assert not changes.ready()
    clock.now = 2.5
    assert changes.ready()
    assert changes.take().paths == [str(file)]


def test_a_growing_file_waits(tmp_path):
    clock, file = Clock(), tmp_path / "download.mp4"
    changes = Changes(quiet=2, clock=clock)
    file.write_bytes(b"x" * 10)
    changes.touch(str(file))
    file.write_bytes(b"x" * 1000)               # still being written (its own events may lag behind)
    clock.now = 3
    assert changes.take().paths == []
    clock.now = 6
    assert changes.take().paths == [str(file)]


@windows
def test_a_locked_file_waits(tmp_path):
    """A file someone holds open without sharing (a download, a document being saved) isn't read yet."""
    clock, file = Clock(), tmp_path / "locked.docx"
    file.write_bytes(b"x")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(str(file), 0x80000000, 0, None, 3, 0, None)   # GENERIC_READ, share nothing
    try:
        changes = Changes(quiet=2, clock=clock)
        changes.touch(str(file))
        clock.now = 3
        assert changes.take().paths == []
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    clock.now = 6
    assert changes.take().paths == [str(file)]


def test_a_deleted_path_settles_at_once(tmp_path):
    clock = Clock()
    changes = Changes(quiet=2, clock=clock)
    changes.touch(str(tmp_path / "gone.jpg"))
    clock.now = 2.5
    assert changes.take().paths == [str(tmp_path / "gone.jpg")]


# Reconciling changes

@pytest.fixture
def indexed(tmp_path):
    root = tmp_path / "files"
    photo(root / "trip" / "IMG_1.png", 7)
    photo(root / "trip" / "IMG_2.png", 9)
    (root / "notes.txt").write_text("banana bread recipe")
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, workers=2)
    indexer.sync(Selection((str(root),)))
    indexer.run(FakeEmbedder())
    return root, store, indexer


def ids(store: Store) -> dict[str, int]:
    return {os.path.relpath(f.path, store.path.parent / "files"): f.id for f in store.files()}


def test_update_new_changed_deleted(indexed):
    root, store, indexer = indexed
    photo(root / "new.png", 11)
    (root / "notes.txt").write_text("banana bread recipe, with walnuts")
    os.utime(root / "notes.txt", (time.time() + 5, time.time() + 5))
    (root / "trip" / "IMG_2.png").unlink()
    summary = indexer.update([str(root / "new.png"), str(root / "notes.txt"), str(root / "trip" / "IMG_2.png")],
                             Selection((str(root),)))
    assert (summary.added, summary.changed, summary.removed) == (1, 1, 1)
    assert sorted(ids(store)) == ["new.png", "notes.txt", os.path.join("trip", "IMG_1.png")]


def test_update_renames_keep_their_vectors(indexed):
    root, store, indexer = indexed
    before, vectors = ids(store), store.vector_count()
    os.replace(root / "trip", root / "holiday")                                   # a folder renamed
    os.replace(root / "notes.txt", root / "recipe.txt")                           # a file renamed
    summary = indexer.update([], Selection((str(root),)), renames=[(str(root / "trip"), str(root / "holiday")),
                                                                    (str(root / "notes.txt"), str(root / "recipe.txt"))])
    assert (summary.moved, summary.added, summary.removed) == (3, 0, 0)
    after = ids(store)
    assert after[os.path.join("holiday", "IMG_1.png")] == before[os.path.join("trip", "IMG_1.png")]
    assert after["recipe.txt"] == before["notes.txt"]
    assert store.vector_count() == vectors and not store.pending()


def test_update_a_folder_deleted_or_moved_in(indexed, tmp_path):
    root, store, indexer = indexed
    outside = tmp_path / "elsewhere" / "album"
    photo(outside / "IMG_9.png", 13)
    os.replace(outside, root / "album")                       # moved in from outside: one "added" for the folder
    import shutil
    shutil.rmtree(root / "trip")                              # deleted: one "removed" for the folder
    summary = indexer.update([str(root / "album"), str(root / "trip")], Selection((str(root),)))
    assert (summary.added, summary.removed) == (1, 2)
    assert sorted(ids(store)) == [os.path.join("album", "IMG_9.png"), "notes.txt"]


def test_update_applies_the_folder_rules(indexed):
    root, store, indexer = indexed
    (root / "project").mkdir()
    (root / "project" / "package.json").write_text("{}")
    (root / "project" / "notes.md").write_text("not for the index: inside a code project")
    photo(root / "project" / "Screenshot 1.png")
    photo(root / "node_modules" / "pkg" / "logo.png")
    summary = indexer.update([str(root / "project" / "notes.md"), str(root / "project" / "Screenshot 1.png"),
                              str(root / "node_modules" / "pkg" / "logo.png")], Selection((str(root),)))
    assert summary.added == 1 and os.path.join("project", "Screenshot 1.png") in ids(store)


def test_an_unplugged_folder_keeps_its_files(indexed, tmp_path):
    root, store, indexer = indexed
    count = len(store.files())
    os.replace(root, tmp_path / "unplugged")                  # the drive goes away
    summary = indexer.sync(Selection((str(root),)))
    assert summary.removed == 0 and len(store.files()) == count


def test_one_writer_at_a_time(tmp_path):
    store = Store(tmp_path / "index.sqlite")
    with writer_lock(store), pytest.raises(Busy), writer_lock(Store(tmp_path / "index.sqlite")):
        pass
    with writer_lock(store):   # free again
        pass


# Windows' notifications

@windows
def test_the_watcher_reports_changes_and_renames(tmp_path):
    changes = Changes(quiet=0.2)
    folder = FolderWatcher(changes)
    folder.watch([str(tmp_path)])
    try:
        photo(tmp_path / "a.png")
        wait_for(lambda: str(tmp_path / "a.png") in changes._paths)
        os.replace(tmp_path / "a.png", tmp_path / "b.png")
        wait_for(lambda: (str(tmp_path / "a.png"), str(tmp_path / "b.png")) in changes._renames)
        time.sleep(0.3)
        batch = changes.take()
        assert (str(tmp_path / "a.png"), str(tmp_path / "b.png")) in batch.renames
        assert str(tmp_path / "b.png") not in batch.paths   # the rename carries it
    finally:
        folder.stop()


@windows
def test_the_watcher_reports_an_overflow(tmp_path, monkeypatch):
    """Changes that don't fit Windows' buffer are lost; the watcher must say so (watchdog doesn't)."""
    monkeypatch.setattr(watcher, "BIG_BUFFER", 1024)
    changes = Changes(quiet=0.2)
    stalled = threading.Event()
    record = changes.touch

    def slow_touch(path):   # the watch thread falls behind once, as a busy PC's would, while files pour in
        if not stalled.is_set():
            stalled.set()
            time.sleep(1.0)
        record(path)

    changes.touch = slow_touch
    folder = FolderWatcher(changes)
    folder.watch([str(tmp_path)])
    try:
        (tmp_path / "first.txt").write_text("x")
        assert stalled.wait(5)
        for i in range(300):
            (tmp_path / f"file_{i:04d}_with_a_long_name_to_fill_the_buffer.txt").write_text("x")
        wait_for(lambda: changes._overflowed)
    finally:
        folder.stop()


@windows
def test_the_watcher_notices_its_folder_going_away(tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    changes = Changes(quiet=0.2)
    folder = FolderWatcher(changes)
    folder.watch([str(root)])
    try:
        import shutil
        shutil.rmtree(root)
        wait_for(lambda: changes._lost or folder.missing([str(root)]))
    finally:
        folder.stop()


# The service

@windows
def test_the_service_keeps_the_index_in_step(tmp_path):
    root = tmp_path / "files"
    photo(root / "IMG_1.png", 7)
    store = Store(tmp_path / "index.sqlite")
    started = []

    def models():
        started.append(1)
        return FakeEmbedder(), lambda: None

    service = IndexService(store, Selection((str(root),)), Indexer(store).options, models, quiet=0.2, idle=0.5)
    stop = threading.Event()
    thread = threading.Thread(target=service.run, args=(stop,), daemon=True)
    thread.start()
    try:
        assert service.ready.wait(10)

        def states():
            return {os.path.basename(f.path): f.state for f in Store(store.path).files()}

        wait_for(lambda: states() == {"IMG_1.png": "done"})
        photo(root / "IMG_2.png", 9)                                          # a new file
        wait_for(lambda: states().get("IMG_2.png") == "done")
        first_id = next(f.id for f in Store(store.path).files() if f.path.endswith("IMG_2.png"))
        os.replace(root / "IMG_2.png", root / "renamed.png")                  # renamed: same entry, new path
        wait_for(lambda: "renamed.png" in states())
        assert next(f.id for f in Store(store.path).files() if f.path.endswith("renamed.png")) == first_id
        (root / "IMG_1.png").unlink()                                         # deleted
        wait_for(lambda: "IMG_1.png" not in states())
        with pytest.raises(Busy), writer_lock(Store(store.path)):           # `dowse index` must wait its turn
            pass
    finally:
        stop.set()
        thread.join(10)
    assert not thread.is_alive()
