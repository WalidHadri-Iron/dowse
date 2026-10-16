"""What Dowse searches, file by file, and how each one is doing: the Library's lists and `dowse files`.

A list is a view of the index and of the user's choices (`Selection`):
- "all": every configured file in the index, including inactive cached files;
- "folders": the configured search roots, with their Active/Inactive switches;
- "inactive": explicitly suspended files and folders (not every child of an inactive folder);
- a watched folder's path: the files in it;
- "named": the files added one by one, including those not in the index (yet, or ever: a key file, a missing one);
- "attention": what couldn't be read, or won't be: failed and skipped files, and added files that can't be searched;
- "removed": exclusions and forgotten sources, kept for Add back; never indexed while removed.
"""

import os
from dataclasses import dataclass, replace
from pathlib import PurePath

from . import crawler, rules
from .rules import Kind
from .selection import Selection, inside, norm
from .store import Store

ALL, NAMED, ATTENTION, REMOVED, FOLDERS, INACTIVE = "all", "named", "attention", "removed", "folders", "inactive"


def search_folders(store: Store, selection: Selection) -> list[dict]:
    """Filter choices from Dowse, not a filesystem scan. Counts include searchable descendants.

    Keep configured roots visible while they wait to be read or are inactive. Subfolders come only from
    indexed files still configured; inactive folders stay visible but cannot be chosen. For individually added
    files outside roots, offer only their containing folder.
    Never offer unrelated disk ancestors or removed sources. Cached inactive rows do not increase counts.
    """
    folders: dict[str, dict] = {}
    roots = {norm(p): p for p in selection.folders if selection.configured(p) is not None}

    def add(path: str, root: str) -> dict:
        key = norm(path)
        if key not in folders:
            folders[key] = {"path": path, "name": PurePath(path).name or path, "root": root,
                            "isRoot": key == norm(root), "count": 0,
                            "active": selection.inactive_by(path) is None}
        return folders[key]

    for root in roots.values():
        add(root, root)
    for path, active in store.db.execute("SELECT path, active FROM files WHERE state IN ('done', 'partial')"):
        if selection.configured(path) is None:
            continue
        searchable = int(bool(active) and selection.decide(path) is not None)
        parent = PurePath(path).parent
        enclosing = [key for key in roots if inside(norm(path), key)]
        if not enclosing:
            add(str(parent), str(parent))["count"] += searchable
            continue
        # One increment per containing folder, including overlapping configured roots.
        outer = min(enclosing, key=len)
        while inside(norm(str(parent)), outer):
            containing = [key for key in enclosing if inside(norm(str(parent)), key)]
            add(str(parent), roots[max(containing, key=len)])["count"] += searchable
            if norm(str(parent)) == outer or parent == parent.parent:
                break
            parent = parent.parent
    return sorted(folders.values(), key=lambda f: (not f["active"], not f["isRoot"], f["name"].casefold(),
                                                  f["path"].casefold()))

# status → what it's called
STATUSES = {"done": "Searchable", "partial": "Partly read", "pending": "Waiting to be read", "skipped": "Skipped",
            "failed": "Couldn't read", "refused": "Can't be searched", "missing": "Not found",
            "removed": "Removed by you", "inactive": "Inactive"}
TROUBLE = ("skipped", "failed", "refused", "missing")
OUTSIDE = ("refused", "missing")   # trouble with an added file that keeps it out of the index


@dataclass(frozen=True)
class Entry:
    path: str
    kind: Kind | None        # None: a folder, or a file of a type Dowse doesn't read
    size: int | None
    mtime: float | None
    status: str              # a key of STATUSES
    detail: str = ""         # why it's skipped, failed or refused
    named: bool = False      # added by name (the folder rules don't apply to it)
    folder: bool = False     # a configured, inactive or removed folder
    active: bool = True
    inactive_by: str | None = None

    @property
    def name(self) -> str:
        return PurePath(self.path).name or self.path

    @property
    def parent(self) -> str:
        return str(PurePath(self.path).parent)

    @property
    def status_text(self) -> str:
        if self.folder and self.status == "done":
            return "Active"
        text = STATUSES[self.status]
        return f"{text}: {self.detail}" if self.detail and self.status in TROUBLE else text


_COLUMNS = "SELECT path, kind, size, mtime, state, COALESCE(detail, '') FROM files"


def _from_rows(rows, named: set[str]) -> list[Entry]:
    return [Entry(path, Kind(kind), size, mtime, state, detail, norm(path) in named)
            for path, kind, size, mtime, state, detail in rows]


def _entries(store: Store, selection: Selection, view: str,
            skip: rules.Skip = rules.NOTHING) -> list[Entry]:
    """The files in `view` (see the module's notes), in no particular order."""
    named = {norm(p) for p in selection.files}
    if view == ALL:
        return _from_rows(store.db.execute(_COLUMNS), named)
    if view == NAMED:
        return _named(store, selection, skip)
    if view == ATTENTION:
        failed = _from_rows(store.db.execute(_COLUMNS + " WHERE state IN ('failed', 'skipped')"), named)
        return failed + [e for e in _named(store, selection, skip) if e.status in OUTSIDE]
    if view == REMOVED:
        return [_removed(path) for path in dict.fromkeys(selection.removed + selection.forgotten)]
    if view == INACTIVE:
        return [replace(_removed(path), status="inactive", inactive_by=path,
                        folder=path in selection.folders or os.path.isdir(path)) for path in selection.inactive]
    if view == FOLDERS:
        return [replace(_removed(path), status="done", active=True, folder=True, kind=None) for path in selection.folders]
    prefix = view.rstrip("\\/") + os.sep
    return _from_rows(store.db.execute(_COLUMNS + " WHERE path >= ? AND path < ?", (prefix, prefix + "\U0010ffff")),
                      named)


def entries(store: Store, selection: Selection, view: str,
            skip: rules.Skip = rules.NOTHING) -> list[Entry]:
    out = []
    for entry in _entries(store, selection, view, skip):
        if view == REMOVED:
            out.append(entry)
            continue
        if selection.configured(entry.path) is None:
            continue   # apply removal immediately, even while the writer is finishing its current step
        blocked = selection.inactive_by(entry.path)
        if view == ATTENTION and blocked:
            continue
        out.append(replace(entry, active=blocked is None, inactive_by=blocked,
                           status="inactive" if blocked else entry.status))
    return out


def _named(store: Store, selection: Selection, skip: rules.Skip) -> list[Entry]:
    """The files added one by one: from the index when they're in it, else why they aren't (yet)."""
    indexed: dict[str, Entry] = {}
    files = list(selection.files)
    for start in range(0, len(files), 500):
        chunk = files[start:start + 500]
        rows = store.db.execute(_COLUMNS + f" WHERE path IN ({','.join('?' * len(chunk))})", chunk)
        indexed |= {norm(e.path): e for e in _from_rows(rows, {norm(p) for p in chunk})}
    out = []
    for path in files:
        entry = indexed.get(norm(path))
        if entry is None:
            entry = _outside(path, selection, skip)
        out.append(entry)
    return out


def _outside(path: str, selection: Selection, skip: rules.Skip) -> Entry:
    """An added file that isn't in the index: on its way, or why not."""
    if selection.inactive_by(path):
        kind = rules.classify(os.path.basename(path))
        return Entry(path, kind if isinstance(kind, Kind) else None, None, None, "inactive", named=True, active=False)
    fate = crawler.check(path, selection, skip)
    kind = rules.classify(os.path.basename(path))
    kind = kind if isinstance(kind, Kind) else None
    if isinstance(fate, crawler.Found):
        return Entry(path, fate.kind, fate.size, fate.mtime, "pending", named=True)
    if fate is None or not os.path.exists(path):
        return Entry(path, kind, None, None, "missing", "moved or deleted", named=True)
    info = os.stat(path)
    return Entry(path, kind, info.st_size, info.st_mtime, "refused", fate, named=True)


def _removed(path: str) -> Entry:
    folder = os.path.isdir(path)
    kind = None if folder else rules.classify(os.path.basename(path))
    try:
        info = os.stat(path)
        size, mtime = (None if folder else info.st_size), info.st_mtime
    except OSError:
        size = mtime = None
    return Entry(path, kind if isinstance(kind, Kind) else None, size, mtime, "removed", folder=folder, active=False)


def add(selection: Selection, paths: list[str],
        skip: rules.Skip = rules.NOTHING) -> tuple[Selection, dict[str, str]]:
    """`selection` with `paths` added, and the files among them that can't be searched, with why. Those that never
    can be (a key file, a type Dowse can't read) aren't kept; a file that's only online is, since it can be read once
    it's downloaded."""
    after = selection.add(paths, skip)
    refused = refusals(paths, after, skip)
    never = {norm(path) for path, why in refused.items() if why != crawler.ONLINE_ONLY}
    if never:
        after = replace(after, files=tuple(f for f in after.files if norm(f) not in never))
    return after, refused


def refusals(paths: list[str], selection: Selection,
             skip: rules.Skip = rules.NOTHING) -> dict[str, str]:
    """Of the files in `paths` (just added to `selection`), those that can't be searched anyway, and why: a key file,
    a type Dowse can't read, a file that's only online."""
    return {path: why for path in paths if os.path.isfile(path) and selection.decide(path) is not None
            and isinstance(why := crawler.check(path, selection, skip), str)}


def counts(store: Store, selection: Selection, skip: rules.Skip = rules.NOTHING) -> dict[str, int]:
    """How many files each view lists (its key: a view's name, or a watched folder's path)."""
    out = {ALL: store.db.execute("SELECT COUNT(*) FROM files").fetchone()[0], NAMED: len(selection.files),
           REMOVED: len(set(selection.removed + selection.forgotten)),
           FOLDERS: len(selection.folders), INACTIVE: len(selection.inactive)}
    failed = store.db.execute("SELECT COUNT(*) FROM files WHERE state IN ('failed', 'skipped')").fetchone()[0]
    out[ATTENTION] = failed + sum(e.status in OUTSIDE for e in _named(store, selection, skip))
    for folder in selection.folders:
        out[folder] = store.count_at(folder)
    return out
