"""Moving a PC from the app's first name (searchpc, data in %LOCALAPPDATA%\\SearchPC) to Dowse: run first thing by
the command line and the app, before anything opens a file. Does nothing when there's nothing to move.

Item by item (the model, the engine, the index, the logs…), each a rename on the same disk, so the 865 MB model
moves at once. Something locked (an Explorer window open in a folder, an antivirus scan) stays where it is and moves
on a later start, while the rest moves now; the old folder goes once it's empty. When the new folder already has an
item, the new one wins, except an empty index (made before the move), which gives way to the real one. Everything
that pointed inside the old folder then points at the new place: the settings' folders, and the index's files
(re-pathed: they keep their vectors).
"""

import os
import sys
from pathlib import Path

from . import paths, settings

OLD_FOLDER = "SearchPC"
OLD_RUN_VALUE = "searchpc"
INDEX_FILES = ("index.sqlite", "index.sqlite-wal", "index.sqlite-shm")


def _old_home() -> Path | None:
    if os.environ.get("DOWSE_HOME"):   # a chosen home is never moved
        return None
    return paths.home().with_name(OLD_FOLDER)


def run() -> bool:
    """Moves what's in the old home into the new one; True if anything moved."""
    old, new = _old_home(), paths.home()
    if old is None or not old.is_dir():
        return False
    new.mkdir(parents=True, exist_ok=True)
    before = _count(new)
    _merge(old, new)
    moved = _count(new) != before
    if moved:
        _repoint(str(old), str(new))
        _startup_entry()
    return moved


def _count(folder: Path) -> int:
    return sum(1 for _ in folder.rglob("*"))


def _empty_index(folder: Path) -> bool:
    index = folder / "index.sqlite"
    if not index.exists():
        return True
    import sqlite3
    from contextlib import closing

    try:
        with closing(sqlite3.connect(index)) as db:   # closed after: `with connect()` alone only ends a transaction
            return db.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    except sqlite3.Error:
        return False


def _merge(source: Path, target: Path) -> bool:
    """Moves `source` into `target`; True when nothing of it is left behind."""
    if not target.exists():
        try:
            source.rename(target)
            return True
        except OSError:
            if not source.is_dir():
                return False
            target.mkdir()   # the folder can't move whole (something in it is open): its items, one by one
    if source.is_dir() and target.is_dir():
        if source.name == "Index" and (source / "index.sqlite").exists() and _empty_index(target):
            try:
                for name in INDEX_FILES:   # the empty index made before the move gives way
                    (target / name).unlink(missing_ok=True)
            except OSError:
                return False   # it's open (the new version is running): next start
        done = all([_merge(child, target / child.name) for child in list(source.iterdir())])
        if done:
            try:
                source.rmdir()
            except OSError:
                return False
        return done
    try:
        source.unlink()   # the new home has its own: the old one is superseded
        return True
    except OSError:
        return False


def _moved(path: str, old: str, new: str) -> str:
    folded = paths.fold(path)
    if folded == paths.fold(old) or folded.startswith(paths.fold(old) + os.sep):
        return new + path[len(old):]
    return path


def _repoint(old: str, new: str) -> None:
    chosen = settings.load()
    for key in ("folders", "excluded"):
        chosen[key] = [_moved(p, old, new) for p in chosen.get(key, [])]
    settings.save(chosen)
    index = paths.index() / "index.sqlite"
    if not index.exists():
        return
    from .store import Store

    store = Store(index)
    try:
        with store.transaction():
            for file_id, path, _, _ in [(i, p, s, m) for p, (i, s, m) in store.known_at(old).items()]:
                store.repath(file_id, _moved(path, old, new))
    finally:
        store.close()


def _startup_entry() -> None:
    """Starting with Windows was the old name's registry value: the same choice, under the new name."""
    if sys.platform != "win32":
        return
    import winreg

    from .desktop import winapi

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_ALL_ACCESS) as key:
            winreg.QueryValueEx(key, OLD_RUN_VALUE)
            winreg.DeleteValue(key, OLD_RUN_VALUE)
    except OSError:
        return
    winapi.set_starts_with_windows(True)
