"""How fast Dowse reads a library and answers a search, on this PC, end to end.

Each run reads the library from nothing in a throwaway data folder (the installed models and runtimes are used where
they are; the live index and settings are never touched), then times searches on the index it made:

- **indexing**: the whole library in one run (`Indexer.sync` + `Indexer.run`, as `dowse index` does), from starting the
  model server to the last file; with `--kinds`, each kind of file again on its own, in a fresh folder, so its own
  pace shows (a run with every kind overlaps reading and embedding, so it's faster than the sum). Each run reports
  its average: seconds per file, and per PDF page or minute of sound or video.
- **search**: 40 everyday queries, each time from typed words to ranked results: the query's embedding (the
  text-only model the app keeps for search) and the ranking (words and meaning). The first search after the model
  wakes is timed apart, with the wake itself.
- **the vector pass**: search compares the query with every vector in the index, so its cost grows with the library;
  that pass alone is timed on synthetic indexes of 100k and 1M vectors.

    uv run python scripts/benchmark.py <folder> [--backend vulkan|cpu] [--kinds] [--out results.json]
"""
import argparse
import json
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dowse import paths, probes, runtime  # noqa: E402
from dowse.embedder import DIM, Embedder  # noqa: E402
from dowse.indexer import Indexer  # noqa: E402
from dowse.reading import Options  # noqa: E402
from dowse.rules import Kind, Skip  # noqa: E402
from dowse.searcher import BLOCK, Searcher, for_meaning  # noqa: E402
from dowse.selection import Selection  # noqa: E402
from dowse.store import Store  # noqa: E402

QUERIES = [   # what a person types (they suit the README's demo library; any library works)
    "what does error E21 mean on the washing machine", "cholesterol results", "car insurance excess",
    "when do we decide on the vector database", "electricity bill from March", "Lisbon photos from June 2025",
    "payslip septmber", "receipt from the pharmacy", "screenshot of the printer error", "photos of my cat",
    "where did I park at the airport", "the podcast part about an emergency fund", "knife skills",
    "the part of the trip video about custard tarts", "the palace on the hill in Sintra",
    "where they say to use a paper clip", "a tram ride through Lisbon", "walking in Tokyo at night",
    "how to make carbonara without cream", "couscous recipe", "letter about a leak in the bathroom",
    "Shinkansen ticket to Kyoto", "rent receipt from Marseille", "facture d'électricité", "وصفة الكسكس",
    "京都での夕食", "une fable de La Fontaine", "pastel de nata", "dinner with Kenji in Kyoto", "a penguin",
    "why were customers charged twice", "diagram of how a search request flows",
    "whiteboard sketch of Atlas with Redis and Kafka", "token bucket rate limiter code",
    "the paper that introduced transformers", "pets in the lease", "photos of a yellow tram in Lisbon",
    "receipt from the bakery in Paris", "can I keep a dog in the flat", "video of custard tarts",
]
NAMES = {Kind.IMAGE: "photos", Kind.SCREENSHOT: "screenshots", Kind.PDF: "PDFs", Kind.DOC: "documents",
         Kind.AUDIO: "recordings", Kind.VIDEO: "videos"}


def fresh_home(live: Path) -> Path:
    """A throwaway data folder: its own index and settings, the installed models and runtimes."""
    home = Path(tempfile.mkdtemp(prefix="dowse-bench-"))
    os.environ["DOWSE_HOME"] = str(home)
    paths.models = lambda: live / "Models"
    paths.runtimes = lambda: live / "Runtime"
    paths.index().mkdir(parents=True, exist_ok=True)
    return home


def contents(store: Store) -> dict:
    """What was read, by kind: files, the segments made from them, pages, and minutes of sound or video."""
    db = store.db
    out = {}
    for kind in Kind:
        files, size = db.execute("SELECT count(*), coalesce(sum(size), 0) FROM files WHERE kind = ? AND state IN "
                                 "('done', 'partial')", (kind.value,)).fetchone()
        if not files:
            continue
        segments = db.execute("SELECT count(*) FROM segments s JOIN files f ON f.id = s.file WHERE f.kind = ? AND "
                              "s.vec IS NOT NULL", (kind.value,)).fetchone()[0]
        entry = {"files": files, "megabytes": round(size / 1e6, 1), "vectors": segments}
        if kind in (Kind.AUDIO, Kind.VIDEO):
            seconds = db.execute("SELECT coalesce(sum(m), 0) FROM (SELECT max(s.loc_end) m FROM segments s JOIN files f "
                                 "ON f.id = s.file WHERE f.kind = ? GROUP BY f.id)", (kind.value,)).fetchone()[0]
            entry["minutes"] = round(seconds / 60, 1)
        if kind is Kind.PDF:
            entry["pages"] = db.execute("SELECT count(DISTINCT s.file || ':' || s.loc) FROM segments s JOIN files f "
                                        "ON f.id = s.file WHERE f.kind = 'pdf'").fetchone()[0]
        out[NAMES[kind]] = entry
    return out


def averages(run: dict) -> dict:
    """Seconds of reading per file (and, for one kind alone, per PDF page or per minute of sound or video), the
    model's start left out."""
    work = run["seconds"] - run["model_start_s"]
    out = {"per_file_s": round(work / max(1, run["files_read"]), 3)}
    if len(run["contents"]) == 1:
        (only,) = run["contents"].values()
        if "pages" in only:
            out["per_page_s"] = round(work / only["pages"], 3)
        if only.get("minutes"):
            out["per_minute_s"] = round(work / only["minutes"], 3)
    return out


def index(library: str, choice: runtime.Choice, live: Path, only: Kind | None = None) -> dict:
    home = fresh_home(live)
    skip = Skip(frozenset(k for k in Kind if only and k is not only))
    options = Options.for_device(choice.device is not None, skip)
    store = Store()
    indexer = Indexer(store, options)
    indexer.sync(Selection(folders=(library,)))
    started = time.perf_counter()
    with runtime.Server(choice) as server, Embedder(server.url, parallel=server.slots, model=server.model) as embedder:
        ready = time.perf_counter()
        read = indexer.run(embedder)
        probes.ensure(store, embedder)
    finished = time.perf_counter()
    result = {"files_read": read, "model_start_s": round(ready - started, 1), "seconds": round(finished - started, 1),
              "contents": contents(store)}
    store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # the size of what's kept, not of what's still in the log
    result["index_mb"] = round(sum(f.stat().st_size for f in paths.index().glob("index.sqlite*")) / 1e6, 1)
    result["average"] = averages(result)
    result["home"] = str(home)
    return result


def search(choice: runtime.Choice, live: Path, home: str) -> dict:
    os.environ["DOWSE_HOME"] = home
    store = Store()
    started = time.perf_counter()
    with runtime.Server(choice, text_only=True) as server, Embedder(server.url, parallel=1, model=server.model) as embedder:
        woke = time.perf_counter()
        probes.ensure(store, embedder)
        searcher = Searcher(store)
        loaded = time.perf_counter()
        first_vector = embedder.query(for_meaning(QUERIES[0]))
        searcher.search(QUERIES[0], first_vector, limit=60)
        first = time.perf_counter()
        for query in QUERIES[:3]:   # warm
            searcher.search(query, embedder.query(for_meaning(query)), limit=60)
        embed, rank, total = [], [], []
        for _ in range(3):
            for query in QUERIES:
                a = time.perf_counter()
                vector = embedder.query(for_meaning(query))
                b = time.perf_counter()
                searcher.search(query, vector, limit=60)
                c = time.perf_counter()
                embed.append(b - a), rank.append(c - b), total.append(c - a)

    def ms(values, q):
        return round(1000 * statistics.quantiles(values, n=100)[q - 1], 1)
    return {"vectors": len(searcher.matrix), "queries": len(total), "wake_s": round(woke - started, 2),
            "load_ms": round(1000 * (loaded - woke)), "first_ms": round(1000 * (first - loaded)),
            "total_ms": {"p50": ms(total, 50), "p95": ms(total, 95)},
            "embed_ms": {"p50": ms(embed, 50), "p95": ms(embed, 95)},
            "rank_ms": {"p50": ms(rank, 50), "p95": ms(rank, 95)}}


def vector_pass() -> dict:
    """The part of a search that grows with the index: one query against every fp16 vector, block by block, as
    `Searcher` does it."""
    import numpy as np
    rng = np.random.default_rng(7)
    query = rng.standard_normal(DIM).astype(np.float32)
    out = {}
    for count in (100_000, 1_000_000):
        matrix = rng.standard_normal((count, DIM)).astype(np.float16)
        scores = np.empty(count, np.float32)
        times = []
        for _ in range(5):
            started = time.perf_counter()
            for start in range(0, count, BLOCK):
                scores[start:start + BLOCK] = matrix[start:start + BLOCK].astype(np.float32) @ query
            times.append(time.perf_counter() - started)
        out[f"{count:,}"] = {"ms": round(1000 * statistics.median(times), 1), "memory_mb": round(matrix.nbytes / 1e6)}
        del matrix
    return out


def machine(choice: runtime.Choice) -> dict:
    def ps(command):
        try:
            return subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True,
                                  timeout=30).stdout.strip()
        except OSError:
            return ""
    return {"cpu": ps("(Get-CimInstance Win32_Processor).Name") or platform.processor(),
            "cores": os.cpu_count(), "memory_gb": round(float(ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
                                                              or 0) / 2**30),
            "gpu": choice.name if choice.device else None, "runtime": choice.describe(), "llama_cpp": runtime.TAG,
            "windows": platform.version()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("library", help="a folder to read from scratch (it isn't changed)")
    parser.add_argument("--backend", choices=["vulkan", "cuda", "cpu"])
    parser.add_argument("--kinds", action="store_true", help="also time each kind of file on its own")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway data folders")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    live = paths.home()
    choice = runtime.choose(args.backend) if args.backend else runtime.load()
    if choice is None:
        sys.exit("Dowse isn't set up yet: run `dowse setup`")
    report = {"machine": machine(choice), "library": Path(args.library).name}
    print(json.dumps(report["machine"]), flush=True)
    homes = []
    full = index(args.library, choice, live)
    homes.append(full.pop("home"))
    report["index"] = full
    print("index", json.dumps(full), flush=True)
    report["search"] = search(choice, live, homes[0])
    print("search", json.dumps(report["search"]), flush=True)
    if args.kinds:
        report["kinds"] = {}
        for kind in Kind:
            alone = index(args.library, choice, live, only=kind)
            homes.append(alone.pop("home"))
            report["kinds"][NAMES[kind]] = alone
            print(NAMES[kind], json.dumps(alone), flush=True)
    report["vector_pass"] = vector_pass()
    print("vector pass", json.dumps(report["vector_pass"]), flush=True)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.keep:
        for home in homes:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
