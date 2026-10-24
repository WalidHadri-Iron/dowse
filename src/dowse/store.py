"""The index: one SQLite file with files, their segments (each with an fp16 vector or none) and an FTS5 table.

Vectors live beside everything else, so a crash can never leave them out of step with the rows that describe them. At
this scale (~100k vectors, ~150 MB) brute force over an in-memory copy takes milliseconds: no vector database needed.
"""

import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np

from . import paths, searchtext
from .rules import ORDER, Kind

SCHEMA = 2
# Marks (M*) count as letters, so Bengali vowel signs and Arabic harakat stay inside their words.
TOKENIZER = "porter unicode61 remove_diacritics 2 categories 'L* N* Co M*'"


class Segment(StrEnum):
    NAME = "name"              # file and folder names (keyword search only)
    IMAGE = "image"            # the picture itself
    OCR = "ocr"                # text OCR read in a picture (keyword search only)
    PAGE = "page"              # a PDF page's text
    PAGE_IMAGE = "page_image"  # a PDF page rendered as a picture (scans, slides)
    CHUNK = "chunk"            # a passage of a document
    AUDIO = "audio"            # a window of sound (a recording, or a video's soundtrack)
    TAG = "tag"                # ID3v2/FLAC music metadata (keyword search only)
    FRAME = "frame"            # a video frame


class Modality(StrEnum):
    NONE = "none"
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


@dataclass
class FileRow:
    id: int
    path: str
    kind: Kind
    size: int
    mtime: float
    state: str
    info: dict


@dataclass
class NewSegment:
    kind: Segment
    modality: Modality
    vector: np.ndarray | None = None
    loc: float | None = None
    loc_end: float | None = None
    excerpt: str | None = None
    name: str = ""       # keyword text matched as names
    body: str = ""       # keyword text matched as content


def default_file() -> Path:
    return paths.index() / "index.sqlite"


class Store:
    def __init__(self, file: Path | None = None):
        self.path = file or default_file()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=60, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA:
            raise RuntimeError(f"this index was made by a newer Dowse (schema {version})")
        if version == 0:
            self.db.executescript(f"""
                BEGIN;
                CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE files(
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',   -- pending | done | partial | skipped | failed
                    detail TEXT,                              -- why skipped or failed, or how far it's been read
                    info TEXT NOT NULL DEFAULT '{{}}',        -- JSON: width, height, taken, pages, read_to…
                    indexed_at REAL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX files_state ON files(state, kind);
                CREATE INDEX files_identity ON files(size, mtime);
                CREATE TABLE segments(
                    id INTEGER PRIMARY KEY,
                    file INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    modality TEXT NOT NULL,
                    loc REAL,                 -- PDF page, passage number, or start second
                    loc_end REAL,
                    excerpt TEXT,
                    vec BLOB                  -- 768 × float16, L2-normalized
                );
                CREATE INDEX segments_file ON segments(file);
                CREATE VIRTUAL TABLE fts USING fts5(name, body, original UNINDEXED, tokenize = "{TOKENIZER}");
                CREATE TRIGGER segments_gone AFTER DELETE ON segments BEGIN
                    DELETE FROM fts WHERE rowid = old.id;
                END;
                PRAGMA user_version = {SCHEMA};
                COMMIT;
                """)
        elif version == 1:
            with self.transaction():
                if self.db.execute("PRAGMA user_version").fetchone()[0] == 1:
                    self.db.execute("ALTER TABLE files ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
                    self.db.execute(f"PRAGMA user_version = {SCHEMA}")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # Meta

    def meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))

    @contextmanager
    def snapshot(self) -> Iterator[None]:
        """A consistent read view without taking the index writer lock (SQLite WAL readers never block it)."""
        if self.db.in_transaction:
            yield
            return
        self.db.execute("BEGIN")
        try:
            yield
        finally:
            self.db.execute("ROLLBACK")

    def set_model_meta(self, key: str, value: str, expected: str | None) -> None:
        """Do not let a late query/Explore worker cache the previous model's vectors after a switch."""
        def write() -> None:
            if self.meta("vectors") not in (None, expected):
                raise RuntimeError("The search model changed while its cache was being prepared. Try again.")
            self.set_meta(key, value)
        if self.db.in_transaction:
            write()
        else:
            with self.transaction():
                write()

    # Files

    def _row(self, row) -> FileRow:
        return FileRow(row[0], row[1], Kind(row[2]), row[3], row[4], row[5], json.loads(row[6] or "{}"))

    _COLUMNS = "id, path, kind, size, mtime, state, info"

    def files(self, where: str = "1", args: Iterable = ()) -> list[FileRow]:
        return [self._row(r) for r in self.db.execute(f"SELECT {self._COLUMNS} FROM files WHERE {where}", tuple(args))]

    def file(self, file_id: int) -> FileRow | None:
        rows = self.files("id = ?", (file_id,))
        return rows[0] if rows else None

    def known(self) -> dict[str, tuple[int, int, float]]:
        """path → (id, size, mtime) of every file in the index."""
        return {row[1]: (row[0], row[2], row[3]) for row in self.db.execute("SELECT id, path, size, mtime FROM files")}

    def known_at(self, path: str) -> dict[str, tuple[int, int, float]]:
        """Like `known`, for the file at `path` and every file under it (when it's, or was, a folder). A range scan
        on the path index: the paths that start with the folder's."""
        folder = path.rstrip("\\/") + os.sep
        rows = self.db.execute("SELECT id, path, size, mtime FROM files WHERE path = ? OR (path >= ? AND path < ?)",
                               (path, folder, folder + "\U0010ffff"))
        return {row[1]: (row[0], row[2], row[3]) for row in rows}

    def count_at(self, folder: str) -> int:
        """How many indexed files are under `folder` (the same range scan as `known_at`, counted)."""
        prefix = folder.rstrip("\\/") + os.sep
        return self.db.execute("SELECT COUNT(*) FROM files WHERE path >= ? AND path < ?",
                               (prefix, prefix + "\U0010ffff")).fetchone()[0]

    def add_file(self, path: str, kind: Kind, size: int, mtime: float) -> int:
        cursor = self.db.execute("INSERT INTO files(path, kind, size, mtime) VALUES(?, ?, ?, ?)",
                                 (path, kind.value, size, mtime))
        file_id = cursor.lastrowid
        self._add_name(file_id, path)
        return file_id

    def _add_name(self, file_id: int, path: str) -> None:
        """The file's name and its folders' names, for keyword search: "Invoices 2026 / acme.pdf"."""
        parts = Path(path).parts
        words = " ".join([Path(path).stem, *parts[-4:-1]]).replace("_", " ").replace("-", " ")
        segment = self.db.execute("INSERT INTO segments(file, kind, modality) VALUES(?, 'name', 'none')",
                                  (file_id,)).lastrowid
        self.db.execute("INSERT INTO fts(rowid, name, body) VALUES(?, ?, '')",
                        (segment, searchtext.normalized(words)))

    def repath(self, file_id: int, path: str) -> None:
        """A file that moved: same content, new place (nothing is embedded again)."""
        self.db.execute("UPDATE files SET path = ? WHERE id = ?", (path, file_id))
        self.db.execute("DELETE FROM segments WHERE file = ? AND kind = 'name'", (file_id,))
        self._add_name(file_id, path)

    def requeue(self, file_id: int, size: int, mtime: float) -> None:
        """A file that changed: everything read from it goes, and it waits to be read again."""
        self.db.execute("DELETE FROM segments WHERE file = ? AND kind != 'name'", (file_id,))
        self.db.execute("UPDATE files SET size = ?, mtime = ?, state = 'pending', detail = NULL, info = '{}', "
                        "indexed_at = NULL WHERE id = ?", (size, mtime, file_id))

    def clear(self) -> None:
        """Forgets every file (their segments and words go with them), keeping the index itself: what "rebuild"
        does while others still have it open (a file in use can't be deleted on Windows)."""
        with self.transaction():
            self.db.execute("DELETE FROM files")   # the model's facts (`meta`: vectors, probes) stay

    def change_model(self, model) -> None:
        """Commit a new embedding space atomically, keeping filenames/text usable while every file is reread.
        Caller holds writer_lock and has stopped the old indexer/query model. Never touches original files."""
        with self.transaction():
            self.db.execute("UPDATE segments SET vec = NULL, modality = 'none' WHERE vec IS NOT NULL")
            self.db.execute("UPDATE files SET state = 'pending', detail = NULL, info = '{}', indexed_at = NULL")
            self.db.execute("DELETE FROM meta WHERE key IN ('probes', 'explore_words')")
            self.set_meta("vectors", model.vector_tag)
            self.set_meta("search_model", model.id)

    def retry_failed(self) -> int:
        failed = [(f.id, f.size, f.mtime) for f in self.files("state = 'failed'")]
        for file_id, size, mtime in failed:
            self.requeue(file_id, size, mtime)
        return len(failed)

    def remove(self, file_ids: Iterable[int]) -> None:
        self.db.executemany("DELETE FROM files WHERE id = ?", [(i,) for i in file_ids])

    def apply_selection(self, selection) -> None:
        """Called by the index's sole writer within reconciliation. Suspended vectors are kept, never searched."""
        identity = json.dumps(selection.to_settings({}), sort_keys=True)
        if self.meta("search_selection") == identity:
            return   # settled watcher changes must not rescan 100k switches for each arriving file
        changes = [(int(selection.decide(path) is not None), file)
                   for file, path, active in self.db.execute("SELECT id, path, active FROM files")
                   if bool(active) != (selection.decide(path) is not None)]
        self.db.executemany("UPDATE files SET active = ? WHERE id = ?", changes)
        self.set_meta("search_selection", identity)

    _ORDER = "CASE kind " + " ".join(f"WHEN '{k.value}' THEN {v}" for k, v in ORDER.items()) + " ELSE 9 END"

    def pending(self, limit: int | None = None, kinds: Iterable[Kind] | None = None) -> list[FileRow]:
        """Waiting files, in reading order: screenshots first, then pictures, PDFs, documents…; newest first."""
        where = "active = 1 AND state = 'pending'"
        if kinds:
            where += " AND kind IN (" + ",".join(f"'{Kind(k).value}'" for k in kinds) + ")"
        sql = f"SELECT {self._COLUMNS} FROM files WHERE {where} ORDER BY {self._ORDER}, mtime DESC"
        return [self._row(r) for r in self.db.execute(sql + (f" LIMIT {int(limit)}" if limit else ""))]

    def partial(self) -> list[FileRow]:
        """Long files read part of the way, in the same order."""
        return self.files(f"active = 1 AND state = 'partial' ORDER BY {self._ORDER}, mtime DESC")

    def finish(self, file_id: int, state: str, detail: str | None = None, info: dict | None = None) -> None:
        self.db.execute("UPDATE files SET state = ?, detail = ?, info = ?, indexed_at = ? WHERE id = ?",
                        (state, detail, json.dumps(info or {}, ensure_ascii=False), time.time(), file_id))

    def add_segments(self, file_id: int, segments: Iterable[NewSegment]) -> None:
        for segment in segments:
            blob = None
            if segment.vector is not None:
                blob = np.asarray(segment.vector, np.float32).astype("<f2").tobytes()
            segment_id = self.db.execute(
                "INSERT INTO segments(file, kind, modality, loc, loc_end, excerpt, vec) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (file_id, segment.kind.value, segment.modality.value, segment.loc, segment.loc_end, segment.excerpt,
                 blob)).lastrowid
            if segment.name or segment.body:
                body = searchtext.normalized(segment.body)
                original = segment.body if body != segment.body else None
                self.db.execute("INSERT INTO fts(rowid, name, body, original) VALUES(?, ?, ?, ?)",
                                (segment_id, searchtext.normalized(segment.name), body, original))

    # Reading back

    def progress(self) -> tuple[int, int]:
        """(files read, files known) over what indexing reads: the first pass's progress, for a status line."""
        done, total = self.db.execute(
            "SELECT SUM(state NOT IN ('pending')), COUNT(*) FROM files WHERE active = 1").fetchone()
        return int(done or 0), int(total or 0)

    def counts(self) -> dict[str, dict[str, int]]:
        """kind → state → files."""
        out: dict[str, dict[str, int]] = {}
        for kind, state, count in self.db.execute("SELECT kind, state, COUNT(*) FROM files GROUP BY kind, state"):
            out.setdefault(kind, {})[state] = count
        return out

    def failures(self, limit: int = 20) -> list[tuple[str, str, str]]:
        return list(self.db.execute(
            "SELECT path, state, detail FROM files WHERE state IN ('failed', 'skipped') ORDER BY indexed_at DESC "
            "LIMIT ?", (limit,)))

    def vector_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM segments WHERE vec IS NOT NULL").fetchone()[0]

    def size_on_disk(self) -> int:
        return sum(p.stat().st_size for p in (self.path, Path(str(self.path) + "-wal")) if p.exists())


class Busy(RuntimeError):
    """Another Dowse process is writing to this index."""


@contextmanager
def writer_lock(store: Store) -> Iterator[None]:
    """One process at a time reads files into an index (a watcher, or `dowse index`), so no file is embedded twice.
    An OS lock on a file beside the index: it goes away with the process, however that ends."""
    path = store.path.with_name(store.path.name + ".writer")
    with open(path, "a+b") as handle:   # closing it releases the lock
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise Busy("another Dowse (a watcher, or an index run) is reading files into this index") from error
        yield
