"""Finds what to index (`rules`, `selection`), without opening a single file.

`crawl` lists everything chosen: the watched folders (walked, with the rules and the user's removals) and the files
added by name. `check` decides about one path the way `crawl` would, for the watcher's changes and for "add this
file". Both decide in `_file` and `folder_fate`, so they can't disagree.
"""

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePath

from . import rules
from .rules import Kind
from .selection import Selection, Source, inside, norm

ONLINE_ONLY = "only online (make it available offline first)"   # why an added file isn't read, for now


@dataclass(frozen=True)
class Found:
    path: str
    kind: Kind
    size: int
    mtime: float


@dataclass
class Crawl:
    files: list[Found] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)   # reason → how many files or folders
    datasets: dict[str, int] = field(default_factory=dict)  # folder → media files left out
    refused: dict[str, str] = field(default_factory=dict)   # file added by name → why it can't be read

    def skip(self, reason: str, count: int = 1) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + count


def _file(path: str, name: str, attrs: int, size: int, mtime: float, in_project: bool,
          skip: rules.Skip, named: bool = False) -> Found | str | None:
    """A file's fate: indexed (`Found`), skipped (the reason), or not a type Dowse reads (None). `named`: added by
    the user by name, so the rules for folders (hidden files, code projects) don't apply; the rest still do."""
    kind = rules.classify(name, rules.NOTHING if named else skip)   # added by name: the user's choice wins
    if kind is None and named:
        return "a type Dowse can't read"
    if kind is None or not isinstance(kind, Kind):
        return kind   # None, or why it's skipped (a key or password file is never read, even by name)
    if rules.cloud_only(attrs):
        return ONLINE_ONLY if named else "online-only (not downloaded)"
    if named:
        return Found(path, kind, size, mtime)
    if rules.hidden(name, attrs):
        return "hidden file"
    if in_project and kind is not Kind.SCREENSHOT:
        return "inside a code project"
    return Found(path, kind, size, mtime)


def _is_project(folder: str) -> bool:
    try:
        return rules.is_project_root(set(os.listdir(folder)))
    except OSError:
        return False


def root_of(path: str, roots: Iterable[str]) -> str | None:
    """The deepest of `roots` that `path` is in, if any."""
    target = norm(path)
    containing = [root for root in roots if inside(target, norm(root))]
    return os.path.abspath(max(containing, key=len)) if containing else None


def folder_fate(folder: str, selection: Selection) -> tuple[bool, bool]:
    """(walked, in a code project): whether a crawl goes into `folder` (it's in a watched folder, and neither the
    rules nor the user's removals keep it out, at any level from the watched folder down), and whether one of those
    folders is a code project."""
    root = root_of(folder, selection.folders)
    if root is None or selection.decide(folder) is not Source.FOLDER:
        return False, False
    in_project = _is_project(root)
    current = root
    for part in PurePath(os.path.relpath(folder, root)).parts if norm(folder) != norm(root) else ():
        current = os.path.join(current, part)
        if rules.folder_skip_reason(current, rules.attributes(current), []) or selection.decide(current) is None:
            return False, in_project
        in_project = in_project or _is_project(current)
    return True, in_project


def _walk(start: str, in_project: bool, selection: Selection, skip: rules.Skip, out: Crawl,
          found: list[Found]) -> None:
    # Inside a walk the user's choices come down to two lookups per entry: a folder the walk reaches is chosen unless
    # it was removed itself (a removed folder's contents are never reached), and likewise a file, unless it was added
    # by name (then it's looked at with the named files). Sets, not `selection.decide`: that scans every choice.
    removed = {norm(p) for p in selection.removed}
    inactive = {norm(p) for p in selection.inactive}
    named = {norm(p) for p in selection.files}

    def walk(folder: str, in_project: bool) -> None:
        try:
            with os.scandir(folder) as listing:
                entries = list(listing)
        except OSError:
            out.skip("unreadable folder")
            return
        in_project = in_project or rules.is_project_root({entry.name for entry in entries})
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if reason := rules.folder_skip_reason(entry.path, rules.attributes(entry), []):
                        out.skip(reason)
                    elif norm(entry.path) in inactive:
                        out.skip("inactive")
                    elif norm(entry.path) in removed:
                        out.skip("removed by you")
                    else:
                        walk(entry.path, in_project)
                    continue
                if not entry.is_file():
                    continue
                path = norm(entry.path)
                if path in inactive:
                    out.skip("inactive")
                    continue
                if path in removed:
                    out.skip("removed by you")
                    continue
                if path in named:
                    continue   # added by name: it's looked at with the named files
                info = entry.stat()
            except OSError:
                continue
            fate = _file(entry.path, entry.name, getattr(info, "st_file_attributes", 0), info.st_size,
                         info.st_mtime, in_project, skip)
            if isinstance(fate, Found):
                found.append(fate)
            elif fate is not None:
                out.skip(fate)

    walk(start, in_project)


def crawl(selection: Selection, skip: rules.Skip = rules.NOTHING,
          within: str | None = None) -> Crawl:
    """Everything chosen: the watched folders' files (or only those under `within`) and the files added by name.
    A watched folder that isn't there (an unplugged drive) is left out: it isn't empty, it's away."""
    out = Crawl()
    candidates: list[Found] = []
    if within is None:
        starts = [(os.path.abspath(f), _is_project(f)) for f in selection.folders
                  if selection.inactive_by(f) is None and os.path.isdir(f)]
    else:
        walked, in_project = folder_fate(within, selection)
        starts = [(os.path.abspath(within), in_project)] if walked and os.path.isdir(within) else []
    for start, in_project in starts:
        _walk(start, in_project, selection, skip, out, candidates)

    roots = {str(PurePath(os.path.abspath(f))) for f in selection.folders}
    out.datasets = rules.dataset_folders(
        [(str(PurePath(f.path).parent), PurePath(f.path).stem, f.kind) for f in candidates], roots)
    seen: set[str] = set()   # watched folders inside other watched folders (older settings) are walked twice
    for found in candidates:
        if str(PurePath(found.path).parent) in out.datasets:
            out.skip("dataset folder")
        elif found.path not in seen:
            seen.add(found.path)
            out.files.append(found)

    for path in selection.files:   # added by name: wherever they are, the rules for folders don't apply
        if selection.inactive_by(path):
            continue
        if within is not None and not inside(norm(path), norm(within)):
            continue
        fate = check(path, selection, skip)
        if isinstance(fate, Found):
            out.files.append(fate)
        elif os.path.exists(path) and fate:
            out.refused[path] = fate
    return out


def check(path: str, selection: Selection, skip: rules.Skip = rules.NOTHING) -> Found | str | None:
    """What a crawl would decide about the file at `path`: `Found`, why it's left out, or None (not a type Dowse
    reads, or not there). Dataset folders are a full crawl's call: a watcher hands a burst of new files to one."""
    source = selection.decide(path)
    if source is None:
        return "not chosen"
    try:
        info = os.stat(path)
    except OSError:
        return None
    attrs = getattr(info, "st_file_attributes", 0)
    if source is Source.NAMED:
        return _file(path, os.path.basename(path), attrs, info.st_size, info.st_mtime, False, skip,
                     named=True)
    walked, in_project = folder_fate(os.path.dirname(path), selection)
    if not walked:
        return "in a skipped folder"
    return _file(path, os.path.basename(path), attrs, info.st_size, info.st_mtime, in_project, skip)
