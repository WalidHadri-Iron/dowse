"""What the user chose to search: folders (searched whole), files added one by one (from anywhere), and what they
removed (files or folders). The one place that decides whether a path belongs in the index.

The most specific choice wins: a file added inside a removed folder is in; a file removed inside a watched folder is
out; a folder removed inside a watched one is out, with everything in it except files added by name. Inside a
watched folder the automatic rules apply (no code projects, no node_modules, no hidden folders…); a file added by
name overrides them, because the user asked for it. Some files are never read, whoever asks: keys and password files,
types Dowse can't read, and files that are only online (reading one would download it).
"""

import functools
import os
from dataclasses import dataclass, replace
from enum import Enum

INCLUDE, EXCLUDE = "include", "exclude"


@functools.lru_cache(maxsize=1 << 16)
def norm(path: str) -> str:
    """A path as compared (Windows: case-insensitive, one kind of slash, no trailing separator). Remembered: every
    result, browse page and map piece asks about its path (75,000 times for 10,700 files listed once)."""
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def inside(path: str, folder: str) -> bool:
    """Is `path` the folder itself or anywhere under it? (Both normalized.)"""
    return path == folder or path.startswith(folder + os.sep)


class Source(Enum):
    """Why a path is in: a watched folder (rules apply), or named by the user (rules don't)."""
    FOLDER = "folder"
    NAMED = "named"


@dataclass(frozen=True)
class Selection:
    folders: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    inactive: tuple[str, ...] = ()   # temporary suspension, including everything inside a folder
    forgotten: tuple[str, ...] = () # removed sources, kept only for Add back / Undo

    @classmethod
    def from_settings(cls, settings: dict) -> "Selection":
        # "excluded" (folders) is what the settings called removals before files could be removed too.
        return cls(tuple(settings.get("folders", [])), tuple(settings.get("files", [])),
                   tuple(settings.get("removed", settings.get("excluded", []))),
                   tuple(settings.get("inactive", [])), tuple(settings.get("forgotten", [])))

    def to_settings(self, settings: dict) -> dict:
        out = {key: value for key, value in settings.items() if key != "excluded"}
        return out | {"folders": list(self.folders), "files": list(self.files), "removed": list(self.removed),
                      "inactive": list(self.inactive), "forgotten": list(self.forgotten)}

    def decide(self, path: str) -> Source | None:
        return None if self.inactive_by(path) else self.configured(path)

    def inactive_by(self, path: str) -> str | None:
        """The outermost suspended path. A named file cannot bypass its inactive parent folder."""
        target = norm(path)
        return next((p for p, normal in self._inactive if inside(target, normal)), None)

    @functools.cached_property
    def _inactive(self) -> list[tuple[str, str]]:
        return [(p, norm(p)) for p in sorted(self.inactive, key=len)]

    @functools.cached_property
    def _choices(self) -> list[tuple[str, str, bool]]:
        """(normalized path, include/exclude, recursive): what `configured` weighs, made once per selection."""
        return ([(norm(p), INCLUDE, True) for p in self.folders] + [(norm(p), INCLUDE, False) for p in self.files]
                + [(norm(p), EXCLUDE, True) for p in self.removed])

    def configured(self, path: str) -> Source | None:
        """Whether `path` is chosen, and how: None (not chosen), FOLDER (in a watched folder: the rules apply), or
        NAMED (added by name: they don't). Only the user's choices count here; `crawler` applies the rules."""
        target = norm(path)
        best: tuple[int, str, bool] | None = None   # (specificity, include/exclude, recursive)
        for chosen, action, recursive in self._choices:
            exact = target == chosen
            if not (exact or (recursive and inside(target, chosen))):
                continue
            specificity = len(chosen) * 2 + (1 if exact else 0)   # deeper wins; a path named exactly beats its folder
            if best is None or specificity > best[0] or (specificity == best[0] and action == EXCLUDE):
                best = (specificity, action, recursive)
        if best is None or best[1] == EXCLUDE:
            return None
        return Source.FOLDER if best[2] else Source.NAMED

    def reaches(self, folder: str) -> bool:
        """Is anything at or under `folder` chosen (the folder itself, a watched folder or a file inside it)?"""
        target = norm(folder)
        if self.inactive_by(folder):
            return False
        return self.decide(folder) is not None or any(inside(norm(p), target) for p in self.folders + self.files)

    def watched(self, path: str) -> bool:
        """Is `path` inside a watched folder (whatever was removed in it)?"""
        target = norm(path)
        return any(inside(target, norm(folder)) for folder in self.folders)

    # Changing: each returns a new selection (the caller saves it)

    def add(self, paths: list[str], skip=None) -> "Selection":
        """The user wants these in. A folder becomes watched, unless a watched folder already covers it; whatever was
        removed in it comes back. A file comes back if it was removed, and is added by name if nothing covers it, or
        if the rules of the folder that does would skip it (a file in a code project, a hidden folder, a type in
        `skip`, the `rules.Skip` in use)."""
        from . import crawler, rules

        skip = skip or rules.NOTHING

        folders, files, removed = list(self.folders), list(self.files), list(self.removed)
        for path in map(os.path.abspath, paths):
            target, is_folder = norm(path), os.path.isdir(path)
            removed = [r for r in removed if not (norm(r) == target or (is_folder and inside(norm(r), target)))]
            current = Selection(tuple(folders), tuple(files), tuple(removed))
            if is_folder:
                if current.decide(path) is not Source.FOLDER:
                    folders = [f for f in folders if not inside(norm(f), target)] + [path]   # nested ones: covered
            else:
                source = current.decide(path)
                rule_skipped = source is Source.FOLDER and not isinstance(crawler.check(path, current, skip),
                                                                          crawler.Found)
                if (source is None or rule_skipped) and target not in {norm(f) for f in files}:
                    files.append(path)
        revived = [os.path.abspath(p) for p in paths]
        def remains(path: str) -> bool:
            return not any(norm(path) == norm(p) or (os.path.isdir(p) and inside(norm(path), norm(p))) for p in revived)
        return replace(self, folders=tuple(sorted(folders)), files=tuple(sorted(files)), removed=tuple(sorted(removed)),
                       inactive=tuple(p for p in self.inactive if remains(p)),
                       forgotten=tuple(p for p in self.forgotten if remains(p)))

    def remove(self, paths: list[str]) -> "Selection":
        """The user wants these out. A watched folder stops being watched, with the files added by name inside it;
        a file added by name is dropped; anything still covered by a watched folder after that is remembered as
        removed. A removal only means something inside a watched folder: the others are dropped."""
        folders, files, removed = list(self.folders), list(self.files), list(self.removed)
        inactive, forgotten = list(self.inactive), list(self.forgotten)
        for path in map(os.path.abspath, paths):
            target = norm(path)
            was_source = any(inside(norm(f), target) for f in folders + files)
            folders = [f for f in folders if not inside(norm(f), target)]
            files = [f for f in files if not inside(norm(f), target)]
            inactive = [f for f in inactive if not inside(norm(f), target)]
            forgotten = [f for f in forgotten if not inside(norm(f), target)]
            current = Selection(tuple(folders), tuple(files), tuple(removed))
            if current.decide(path) is not None and target not in {norm(r) for r in removed}:
                removed.append(path)
            watching = Selection(tuple(folders))
            removed = [r for r in removed if watching.watched(r)]
            if was_source and not watching.watched(path) and target not in {norm(f) for f in forgotten}:
                forgotten.append(path)
        return replace(self, folders=tuple(sorted(folders)), files=tuple(sorted(files)), removed=tuple(sorted(removed)),
                       inactive=tuple(sorted(inactive)), forgotten=tuple(sorted(forgotten)))

    def deactivate(self, paths: list[str]) -> "Selection":
        inactive = list(self.inactive)
        for path in map(os.path.abspath, paths):
            if self.configured(path) is not None and self.inactive_by(path) is None:
                inactive.append(path)
        return replace(self, inactive=tuple(sorted(inactive)))

    def activate(self, paths: list[str]) -> "Selection":
        wanted = {norm(p) for p in paths}
        # Only the requested switch is changed; child suspensions survive activating their parent.
        return replace(self, inactive=tuple(p for p in self.inactive if norm(p) not in wanted))
