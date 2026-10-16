"""What you went back to: the files you opened (or showed in their folder, or copied) and the words you'd searched
for them. Kept on this PC only, beside the index (so its encryption covers it too), never sent anywhere, and
cleared from Settings.

A search counts only once something is opened from it: what's typed on the way ("ele", "elect") never is, and a
search that found nothing worth opening isn't one to suggest again. It gives two things back: the files most recently
gone back to (an empty search shows them first), and earlier searches that start the way a new one does, each with
the file opened from it last time.
"""

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import paths, searchtext

MOST = 500   # entries kept: a year of going back to things, and a file read in a moment


def file() -> Path:
    return paths.index() / "history.json"


@dataclass
class Entry:
    path: str
    query: str | None   # what was searched for it (None: opened from Explore, an aisle, or "more like this")
    at: float


def folded(query: str) -> str:
    """A query as it's compared: case and spacing aside ("Electricity  Bill" ~ "electricity bill"). Accents stay:
    taking marks off would change words in scripts where they're letters."""
    return " ".join(searchtext.normalized(query).lower().split())


class History:
    def __init__(self, where: Path | None = None):
        self.where = where or file()
        self._lock = threading.Lock()
        self._entries: list[Entry] | None = None   # newest first, read on first use

    def opened(self, path: str, query: str | None = None, now: float | None = None) -> None:
        """`path` was gone back to, found by `query`. The same file found by the same words is one entry, moved up."""
        query = " ".join(query.split()) if query and query.strip() else None
        with self._lock:
            entries = self._load()
            key = folded(query) if query else None
            entries[:] = [e for e in entries if not (e.path == path and (folded(e.query) if e.query else None) == key)]
            entries.insert(0, Entry(path, query, time.time() if now is None else now))
            del entries[MOST:]
            self._save(entries)

    def recent(self, limit: int = 8, keep=lambda path: True) -> list[str]:
        """The files most recently gone back to, newest first, each once; `keep` says which may be shown (still
        searched, still there)."""
        out: list[str] = []
        with self._lock:
            for entry in self._load():
                if entry.path not in out and keep(entry.path):
                    out.append(entry.path)
                    if len(out) >= limit:
                        break
        return out

    def queries(self, typed: str = "", limit: int = 5, keep=lambda path: True) -> list[dict]:
        """Earlier searches that start the way `typed` does (all of them when it's empty), newest first, each once
        with the file opened from it last time: {"query", "path", "at"}. One already typed in full isn't suggested
        again, but still says what was opened from it (`exact`)."""
        start = folded(typed)
        out: dict[str, dict] = {}
        with self._lock:
            for entry in self._load():
                if not entry.query or not keep(entry.path):
                    continue
                key = folded(entry.query)
                if key in out or not key.startswith(start):
                    continue
                out[key] = {"query": entry.query, "path": entry.path, "at": entry.at, "exact": key == start}
                if len(out) >= limit + 1:
                    break
        found = list(out.values())
        exact = [q for q in found if q["exact"]]
        return exact + [q for q in found if not q["exact"]][:limit]

    def forget(self, keep=lambda path: False) -> None:
        """Clears it (or keeps only the entries for paths `keep` accepts)."""
        with self._lock:
            entries = [e for e in self._load() if keep(e.path)]
            self._entries = entries
            if entries:
                self._save(entries)
            else:
                self.where.unlink(missing_ok=True)

    def _load(self) -> list[Entry]:
        if self._entries is None:
            try:
                raw = json.loads(self.where.read_text(encoding="utf-8"))
                self._entries = [Entry(e["path"], e.get("query"), float(e["at"])) for e in raw.get("entries", [])]
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self._entries = []
        return self._entries

    def _save(self, entries: list[Entry]) -> None:
        self.where.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.where.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "entries": [asdict(e) for e in entries]}, ensure_ascii=False),
                             encoding="utf-8")
        temporary.replace(self.where)   # never a half-written history
