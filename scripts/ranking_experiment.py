"""Ranking experiment: the plain fusion against each of Dowse's corrections, alone and together, on the eval's
testbed alone and on the whole index (the testbed plus whatever else is indexed, as distractors).

    uv run python scripts/ranking_experiment.py evals/testbed.json [evals/hard.json] [--fresh [--reuse]] [--cpu]
        [--among <index.sqlite>]

Prints R@1, R@5, MRR, strong results a query, and "book noise": how many queries that aren't about the testbed's
books have one among their strong results; then, for Dowse's ranking, the same by query tag. Queries that expect
nothing ("absent") count apart: a strong result for one is a false alarm. Run it before and after any ranking change;
searcher.py's docstring records what it measured last.

`--fresh` reads the testbed again into an index of its own with the readers as they are now (what a change to
reading needs: the whole index holds what was read before it), `--cpu` with the CPU's reading options.

`--among` adds a condition at real size: the testbed (as read for the first condition) put among another index's
files, read with the same model: thousands of files that answer none of the queries, or answer some of them as well
(a real library has its own dogs and receipts). A miss names what came first, so the two can be told apart.
"""

import inspect
import json
import os
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dowse import paths, probes, runtime  # noqa: E402
from dowse import searcher as searching  # noqa: E402
from dowse.cli import _expected  # noqa: E402
from dowse.embedder import Embedder  # noqa: E402
from dowse.indexer import Indexer  # noqa: E402
from dowse.reading import Options  # noqa: E402
from dowse.searcher import Ranking, Searcher, strong_count  # noqa: E402
from dowse.selection import Selection  # noqa: E402
from dowse.store import Store, default_file  # noqa: E402

BOOKS = ("books/pride and prejudice.txt", "books/sherlock holmes.pdf")
CHOSEN = Ranking()
PLAIN = Ranking(sidak=False, hub=0, rare_words=False)
CONFIGS = {
    "no corrections": PLAIN,
    "  + best of n": replace(PLAIN, sidak=True),
    "  + rare words together": replace(PLAIN, rare_words=True),
    "  + hubs (0.5)": replace(PLAIN, hub=0.5),
    "Dowse (all three)": CHOSEN,
    "  but no best of n": replace(CHOSEN, sidak=False),
    "  but no rare words": replace(CHOSEN, rare_words=False),
    "  but no hubs": replace(CHOSEN, hub=0),
    "  but hubs at 1.0": replace(CHOSEN, hub=1.0),
    "  but no query filters": replace(CHOSEN, filters=False),
    "  but no spelling": replace(CHOSEN, spelling=False),
    "  but no clear check": replace(CHOSEN, clear=False),
    "  but 0 probes may outrank": replace(CHOSEN, outranked=0),
    "  but 3 probes may outrank": replace(CHOSEN, outranked=3),
}


def testbed_only(base: Path) -> Store:
    """The testbed's rows copied out of the index into a store of their own (nothing is embedded again)."""
    target = paths.home() / "Experiments" / "testbed-only.sqlite"
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    Store(target).close()
    db = sqlite3.connect(target)
    db.execute("ATTACH ? AS source", (str(default_file()),))
    db.execute("INSERT INTO files SELECT * FROM source.files WHERE path LIKE ?", (str(base) + os.sep + "%",))
    db.execute("INSERT INTO segments SELECT * FROM source.segments WHERE file IN (SELECT id FROM files)")
    db.execute("INSERT INTO fts(rowid, name, body, original) SELECT rowid, name, body, original FROM source.fts "
               "WHERE rowid IN (SELECT id FROM segments)")
    db.execute("INSERT INTO meta SELECT * FROM source.meta")
    db.commit()
    db.close()
    return Store(target)


def fresh(base: Path, cpu: bool) -> Store:
    """The testbed read again, from nothing, into a store of its own, with today's readers."""
    target = paths.home() / "Experiments" / ("testbed-fresh-cpu.sqlite" if cpu else "testbed-fresh.sqlite")
    target.parent.mkdir(parents=True, exist_ok=True)
    if "--reuse" in sys.argv and target.exists():   # the last fresh read, for a change to ranking only
        return Store(target)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    store = Store(target)
    indexer = Indexer(store, Options.for_device(gpu=not cpu))
    indexer.check_vectors()
    indexer.sync(Selection(folders=(str(base),)))
    choice = runtime.load()
    with runtime.Server(choice) as server, Embedder(server.url, parallel=server.slots, model=server.model) as embedder:
        indexer.run(embedder)
        probes.ensure(store, embedder)
    return store


def among(testbed: Store, other: Path) -> Store:
    """The testbed's files added to a copy of another index (the same model's), under new ids."""
    target = paths.home() / "Experiments" / "testbed-among.sqlite"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{other}?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    source.backup(copy)
    source.close()
    tag = copy.execute("SELECT value FROM meta WHERE key = 'vectors'").fetchone()
    if tag is None or tag[0] != testbed.meta("vectors"):
        raise SystemExit(f"{other} holds another model's vectors ({tag and tag[0]}, not {testbed.meta('vectors')})")
    files, segments = (copy.execute(f"SELECT coalesce(max(id), 0) FROM {t}").fetchone()[0] for t in ("files", "segments"))
    copy.execute("ATTACH ? AS testbed", (str(testbed.path),))
    with copy:
        copy.execute("INSERT INTO files SELECT id + ?, path, kind, size, mtime, state, detail, info, indexed_at, active "
                     "FROM testbed.files", (files,))
        copy.execute("INSERT INTO segments SELECT id + ?, file + ?, kind, modality, loc, loc_end, excerpt, vec "
                     "FROM testbed.segments", (segments, files))
        copy.execute("INSERT INTO fts(rowid, name, body, original) SELECT rowid + ?, name, body, original FROM testbed.fts",
                     (segments,))
    copy.close()
    return Store(target)


def main() -> None:
    other = Path(sys.argv[sys.argv.index("--among") + 1]) if "--among" in sys.argv else None
    files = [a for a in sys.argv[1:] if not a.startswith("--") and a != str(other)]
    specs = [json.loads(Path(f).read_text(encoding="utf-8")) for f in files]
    queries = [q for spec in specs for q in spec["queries"]]
    base = Path(os.path.expanduser(specs[0]["base"]))
    now = next((datetime.strptime(s["now"], "%Y-%m-%d %H:%M").timestamp() for s in specs if "now" in s), None)
    books = {os.path.normcase(str(base / b)) for b in BOOKS}
    if "--fresh" in sys.argv:
        stores = {"testbed read now" + (" (CPU options)" if "--cpu" in sys.argv else ""): fresh(base, "--cpu" in sys.argv)}
    else:
        stores = {"testbed alone": testbed_only(base), "whole index": Store()}
    if other:
        stores[f"testbed among {other.parent.parent.name}'s files"] = among(next(iter(stores.values())), other)
    # What the model is asked: the query less what only filters (a date), when the searcher knows how
    asked = getattr(searching, "for_meaning", lambda text, now=None: text)
    timed = "now" in inspect.signature(Searcher.search).parameters
    with runtime.Server(runtime.load(), text_only=True) as server, Embedder(server.url, parallel=1) as embedder:
        vectors = {q["q"]: embedder.query(asked(q["q"], now=now)) for q in queries}
        whole = {q["q"]: embedder.query(q["q"]) for q in queries}   # for rankings without the query filters
        for store in stores.values():
            probes.ensure(store, embedder)
    for condition, store in stores.items():
        print(f"\n== {condition}: {store.vector_count()} vectors, {len(queries)} queries")
        for name, ranking in CONFIGS.items():
            searcher = Searcher(store, ranking)
            ranks, strong, noise, misses, alarms, by_tag, unsure = [], [], 0, [], [], {}, []
            for query in queries:
                vector = vectors[query["q"]] if getattr(ranking, "filters", True) else whole[query["q"]]
                hits = searcher.search(query["q"], vector, limit=10, **({"now": now} if timed else {}))
                count = strong_count(hits)
                if not query["expect"]:   # nothing to find: any strong result is a false alarm
                    if count:
                        alarms.append(f"{query['q'][:24]}({os.path.basename(hits[0].path)[:18]})")
                    by_tag.setdefault(query.get("tag", ""), []).append(0 if count else 1)
                    continue
                rank = next((i + 1 for i, h in enumerate(hits) if any(_expected(h, base, e) for e in query["expect"])),
                            None)
                ranks.append(rank)
                strong.append(count)
                by_tag.setdefault(query.get("tag", ""), []).append(rank)
                if query.get("tag") != "book" and any(os.path.normcase(h.path) in books for h in hits[:count]):
                    noise += 1
                if rank != 1:
                    first = f"<{os.path.basename(hits[0].path)[:18]}" if hits else ""
                    misses.append(f"{query['q'][:24]}({'-' if rank is None else f'#{rank}'}{first})")
                elif not count:   # found first, but not said to be found
                    unsure.append(query["q"][:24])
            total = len(ranks)
            absent = sum(1 for q in queries if not q["expect"])
            print(f"{name:26s} R@1 {sum(r == 1 for r in ranks) / total:.3f}  "
                  f"R@5 {sum(bool(r) and r <= 5 for r in ranks) / total:.3f}  "
                  f"MRR {sum(1 / r for r in ranks if r) / total:.3f}  strong {np.mean(strong):.1f}  "
                  f"book noise {noise:2d}" + (f"  false alarms {len(alarms)}/{absent}" if absent else "") +
                  f"  unsure {len(unsure)}  | {', '.join(misses + alarms)}" +
                  (f"  || unsure: {', '.join(unsure)}" if unsure else ""))
            if ranking == CHOSEN:
                chosen_tags = by_tag
        print("  Dowse by tag (R@1, or for absent: share with no strong result):")
        for tag, values in sorted(chosen_tags.items()):
            if tag == "absent":
                print(f"    {tag:14s} {sum(values) / len(values):.2f}  ({len(values)})")
            else:
                print(f"    {tag:14s} {sum(r == 1 for r in values) / len(values):.2f}  ({len(values)})")


if __name__ == "__main__":
    main()
