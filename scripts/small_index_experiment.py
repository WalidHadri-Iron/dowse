"""Small indexes: does a search find its answer when the index holds only a few files (someone trying Dowse on one
folder, or the first minutes of reading)? The eval's queries are asked of small indexes cut from the testbed: each
query's answer with k − 1 other files drawn at random (seeded), and for the queries with no answer, k files at random.
Compared: meaning scored on one scale for the whole index (`searcher.SMALL = 0`) and as Dowse scores it now.

    uv run python scripts/small_index_experiment.py evals/testbed.json [evals/hard.json] [--sizes 3,5,10,20,50,100]
        [--seeds 5]

Prints for each size: found (the answer first and said to be found), R@1 (first, said or not), strong results a
query, and false alarms (a query with no answer given a strong result). Reads the testbed as ranking_experiment.py
`--fresh` last read it (Experiments/testbed-fresh.sqlite); run that first.
"""

import argparse
import copy
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dowse import paths, runtime  # noqa: E402
from dowse import searcher as searching  # noqa: E402
from dowse.cli import _expected  # noqa: E402
from dowse.embedder import Embedder  # noqa: E402
from dowse.searcher import Searcher, strong_count  # noqa: E402
from dowse.store import Store  # noqa: E402


def cut(whole: Searcher, files: set[int]) -> Searcher:
    """`whole` as if the index held only `files` (its vectors, kinds and dates; words still come from the whole
    index's table, filtered to these files)."""
    part = copy.copy(whole)
    rows = np.isin(whole.row_file, list(files))
    for name in ("row_segment", "row_file", "row_modality", "matrix", "row_hub"):
        setattr(part, name, getattr(whole, name)[rows])
    part.file_kind = {f: k for f, k in whole.file_kind.items() if f in files}
    part.file_vectors = {f: n for f, n in whole.file_vectors.items() if f in files}
    part.file_time = {f: t for f, t in whole.file_time.items() if f in files}
    part._scopes = {}
    return part


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("evals", nargs="+")
    parser.add_argument("--sizes", default="3,5,10,20,50,100")
    parser.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args()
    specs = [json.loads(Path(f).read_text(encoding="utf-8")) for f in args.evals]
    queries = [q for spec in specs for q in spec["queries"]]
    base = Path(os.path.expanduser(specs[0]["base"]))
    now = next((datetime.strptime(s["now"], "%Y-%m-%d %H:%M").timestamp() for s in specs if "now" in s), None)
    target = paths.home() / "Experiments" / "testbed-fresh.sqlite"
    if not target.exists():
        raise SystemExit(f"{target} isn't there: run scripts/ranking_experiment.py --fresh first")
    store = Store(target)
    whole = Searcher(store)
    path_of = dict(store.db.execute("SELECT id, path FROM files WHERE active = 1"))
    everything = sorted(f for f in whole.file_kind if f in whole.file_vectors)
    with runtime.Server(runtime.load(), text_only=True) as server, Embedder(server.url, parallel=1) as embedder:
        vectors = {q["q"]: embedder.query(searching.for_meaning(q["q"], now=now)) for q in queries}
    print(f"{len(everything)} files with vectors in the testbed, {len(queries)} queries, {args.seeds} draws each")

    now_small = searching.SMALL
    for size in (int(s) for s in args.sizes.split(",")):
        for label, small in (("one scale", 0), ("Dowse now", now_small)):
            searching.SMALL = small
            found = first = alarms = absent = asked = 0
            strong, vectors_held = [], []
            for seed in range(args.seeds):
                for number, query in enumerate(queries):
                    draw = random.Random(f"{seed}/{number}/{size}")
                    answers = {f for f, p in path_of.items() if f in whole.file_vectors and
                               any(os.path.normcase(p) == os.path.normcase(str(base / e.split("@")[0]))
                                   for e in query["expect"])}
                    others = [f for f in everything if f not in answers]
                    chosen = set(list(answers)[:1]) | set(draw.sample(others, size - min(len(answers), 1)))
                    part = cut(whole, chosen)
                    vectors_held.append(len(part.matrix))
                    hits = part.search(query["q"], vectors[query["q"]], limit=10, now=now)
                    count = strong_count(hits)
                    if not query["expect"]:
                        absent += 1
                        alarms += bool(count)
                        continue
                    if not answers:
                        continue
                    asked += 1
                    top = bool(hits) and any(_expected(hits[0], base, e) for e in query["expect"])
                    first += top
                    found += top and count > 0
                    strong.append(count)
            print(f"{size:4d} files (~{np.mean(vectors_held):.0f} vectors)  {label:10s}  found {found / asked:.3f}  "
                  f"R@1 {first / asked:.3f}  strong {np.mean(strong):.2f}  false alarms {alarms}/{absent}")
        searching.SMALL = now_small


if __name__ == "__main__":
    main()
