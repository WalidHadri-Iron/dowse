"""Dowse's command line: set up, index, search and measure, from a terminal."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import catalog, migrate, modelfiles, paths, probes, runtime, settings
from .embedder import Embedder
from .fmt import clock
from .indexer import Indexer, Options
from .rules import Kind, Skip
from .searcher import Searcher, for_meaning, strong_count
from .selection import Selection
from .store import Busy, Store, writer_lock


def _choice() -> runtime.Choice:
    choice = runtime.load()
    if choice is None or not runtime.installed(choice.backend) or modelfiles.missing():
        sys.exit("Dowse isn't set up yet: run `dowse setup`")
    return choice


def _options(choice: runtime.Choice) -> Options:
    """How files are read on `choice`'s device, skipping the types chosen in the settings."""
    options = Options.for_device(choice.device is not None, Skip.from_settings(settings.load()))
    options.embed_media = modelfiles.get().media is not None
    return options


def _bar(label: str):
    last = [0.0]

    def show(done: int, total: int) -> None:
        now = time.monotonic()
        if now - last[0] < 0.25 and done != total:
            return
        last[0] = now
        share = f"{done * 100 // total:3d}%" if total else ""
        print(f"\r  {label}: {done / 2**20:7.1f} MB {share}", end="", flush=True)

    return show


# Commands

def cmd_setup(args) -> None:
    print(f"Model: {modelfiles.get().name} → {paths.models()}")
    for file in modelfiles.missing():
        modelfiles.download(progress=_bar(file.name))
        print()
    print(f"Runtime: llama.cpp {runtime.TAG} ({args.backend})")
    choice = runtime.choose(args.backend, args.device, progress=_bar("runtime"))
    runtime.save(choice)
    print(f"\nRunning on: {choice.describe()}")
    print(runtime.environment_summary())


def cmd_status(args) -> None:
    choice = runtime.load()
    print(f"Home: {paths.home()}")
    print(f"Runtime: {choice.describe() if choice else 'not set up'}")
    print(runtime.environment_summary())
    missing = modelfiles.missing()
    print("Model: " + ("ready" if not missing else "missing " + ", ".join(f.name for f in missing)))
    chosen = Selection.from_settings(settings.load())
    print("Folders: " + (", ".join(chosen.folders) or "none yet"))
    if chosen.files or chosen.removed:
        print(f"Added one by one: {len(chosen.files)} · removed: {len(chosen.removed)} (`dowse files added`, "
              "`dowse files removed`)")
    store = Store()
    counts = store.counts()
    for kind in Kind:
        if kind.value in counts:
            states = ", ".join(f"{n} {state}" for state, n in sorted(counts[kind.value].items()))
            print(f"  {kind.value:10s} {states}")
    print(f"Vectors: {store.vector_count()} · index {store.size_on_disk() / 2**20:.1f} MB")
    if args.verbose:
        for path, state, detail in store.failures():
            print(f"  {state}: {path} ({detail})")


def cmd_index(args) -> None:
    """Brings the index in step with what's chosen (adding `args.folders` to it first), then reads what's new."""
    if args.folders:
        _change("add", args.folders, quiet=True)
    chosen = Selection.from_settings(settings.load())
    if not (chosen.folders or chosen.files):
        sys.exit("which folders? `dowse index <folder> …` (they're remembered)")
    choice = _choice()
    store = Store()
    try:
        with writer_lock(store):
            _index(args, chosen, choice, store)
    except Busy as error:
        sys.exit(f"{error}. Stop it first, or let it do the work.")


def _index(args, chosen: Selection, choice: runtime.Choice, store: Store) -> None:
    indexer = Indexer(store, _options(choice))
    started = time.monotonic()
    summary = indexer.sync(chosen)
    if args.retry:
        with store.transaction():
            print(f"{store.retry_failed()} failed files queued again")
    print(f"{summary.found} files: {summary.added} new, {summary.changed} changed, {summary.moved} moved, "
          f"{summary.removed} removed ({time.monotonic() - started:.1f} s)")
    for reason, count in sorted(summary.skipped.items(), key=lambda item: -item[1]):
        print(f"  skipped {count} · {reason}")
    for folder, count in summary.datasets.items():
        print(f"  dataset left out: {folder} ({count} files)")
    for path, why in summary.refused.items():
        print(f"  can't search {path}: {why}")
    if not store.pending(1) and not store.partial():
        print("Nothing to read.")
        return
    print(f"Reading on {choice.describe()}…")
    started = time.monotonic()
    with runtime.Server(choice) as server, Embedder(server.url, parallel=server.slots, model=server.model) as embedder:
        print(f"  model ready in {time.monotonic() - started:.1f} s")

        def progress(p) -> None:
            mark = {"done": "✓", "partial": "…", "failed": "✗", "skipped": "–"}.get(p.state, "?")
            count = f"[{p.done}/{p.total}] " if p.total else "[later] "
            print(f"  {count}{mark} {p.path}" + (f"  ({p.detail})" if p.detail else ""))

        try:
            done = indexer.run(embedder, on_progress=progress, limit=args.limit)
        except KeyboardInterrupt:
            print("Stopped; the next run goes on from here.")
            return
        probes.ensure(store, embedder)
    print(f"Read {done} files in {time.monotonic() - started:.1f} s.")


def cmd_watch(args) -> None:
    """Keeps the index in step with the remembered folders, live, until Ctrl+C."""
    import threading

    from .service import IndexService, model_factory

    chosen = Selection.from_settings(settings.load())
    if not (chosen.folders or chosen.files):
        sys.exit("nothing chosen yet: `dowse index <folder> …` or `dowse add <file> …` first")
    choice = _choice()
    store = Store()

    def show(event) -> None:
        stamp = time.strftime("%H:%M:%S")
        if event.kind == "read" and event.progress:
            p = event.progress
            mark = {"done": "✓", "partial": "…", "failed": "✗", "skipped": "–"}.get(p.state, "?")
            print(f"{stamp} {mark} {p.path}" + (f"  ({p.detail})" if p.detail else ""), flush=True)
        else:
            print(f"{stamp} {event.message}", flush=True)

    service = IndexService(store, chosen, _options(choice), model_factory(choice),
                           on_event=show)
    stop = threading.Event()
    extra = f" and {len(chosen.files)} files added one by one" if chosen.files else ""
    print(f"Watching {', '.join(chosen.folders) or 'no folders'}{extra} on {choice.describe()}. Ctrl+C to stop.",
          flush=True)
    thread = threading.Thread(target=_run_service, args=(service, stop), name="service", daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        print("Stopping…", flush=True)
        stop.set()
        thread.join(30)


def _run_service(service, stop) -> None:
    try:
        service.run(stop)
    except Busy as error:
        print(f"{error}.", flush=True)


def _change(op: str, paths: list[str], quiet: bool = False) -> None:
    """Adds files and folders to what Dowse searches, or takes them out: through the app when it's running (it
    applies the change at once, and says so), else in the settings, for the next time something indexes."""
    from .desktop import launch

    paths = [os.path.abspath(p) for p in paths]
    label = {"add": "Added", "remove": "Removed", "activate": "Activated", "deactivate": "Deactivated"}[op]
    missing = [p for p in paths if op == "add" and not os.path.exists(p)]
    if missing:
        sys.exit(f"not found: {', '.join(missing)}")
    if launch.send({"op": op, "paths": paths}):
        if not quiet:
            print(f"{label}: the app is applying it now.")
        return
    chosen = settings.load()
    before = Selection.from_settings(chosen)
    skip = Skip.from_settings(chosen)
    after, refused = catalog.add(before, paths, skip) if op == "add" else (getattr(before, op)(paths), {})
    settings.save(after.to_settings(chosen))
    if quiet:
        return
    if after == before:
        print("Nothing changed: " + ("already searched." if op == "add" else "not searched."))
    else:
        print(f"{label}. The index follows the next time Dowse runs (the app, "
              "`dowse watch` or `dowse index`).")
    for path, why in refused.items():
        print(f"  can't search {path}: {why}")


def cmd_add(args) -> None:
    _change("add", args.paths)


def cmd_remove(args) -> None:
    _change("remove", args.paths)


def cmd_files(args) -> None:
    """Lists what Dowse searches, file by file, with how each one is doing (`catalog`)."""
    views = {"all": catalog.ALL, "added": catalog.NAMED, "attention": catalog.ATTENTION, "removed": catalog.REMOVED,
             "folders": catalog.FOLDERS, "inactive": catalog.INACTIVE}
    chosen = Selection.from_settings(settings.load())
    view = views.get(args.view) or os.path.abspath(args.view)
    if view not in views.values() and view not in chosen.folders:
        sys.exit(f"{args.view}: not a watched folder; or one of {', '.join(views)}")
    store = Store()
    entries = sorted(catalog.entries(store, chosen, view), key=lambda e: e.path.casefold())
    if args.json:
        print(json.dumps([{"path": e.path, "kind": e.kind.value if e.kind else None, "status": e.status,
                           "detail": e.detail, "added_by_name": e.named} for e in entries], ensure_ascii=False))
        return
    marks = {"done": "✓", "partial": "…", "pending": "·", "skipped": "–", "failed": "✗", "refused": "✗",
             "missing": "?", "removed": "−", "inactive": "○"}
    for entry in entries:
        print(f"{marks[entry.status]} {entry.path}" + (f"  ({entry.status_text})" if entry.status != "done" else ""))
    print(f"\n{len(entries):,} {'item' if view == catalog.REMOVED else 'file'}{'s' if len(entries) != 1 else ''}")


def cmd_reset(args) -> None:
    """Deletes the index (never your files): everything is read again on the next `index`."""
    from .store import default_file

    store = Store()
    try:
        with writer_lock(store):
            store.close()
            removed = [p for p in (default_file(), Path(f"{default_file()}-wal"), Path(f"{default_file()}-shm"))
                       if p.exists()]
            for path in removed:
                path.unlink()
    except Busy as error:
        sys.exit(f"{error}: stop it before deleting the index.")
    print("Index deleted." if removed else "There was no index.")


def _place(kind: Kind, loc: float, end: float | None) -> str:
    if kind is Kind.PDF:
        return f"p.{int(loc)}"
    if kind is Kind.DOC:
        return f"#{int(loc) + 1}"
    return f"@{clock(loc)}–{clock(end if end is not None else loc)}"


def _print_hits(hits, as_json: bool) -> None:
    if as_json:
        print(json.dumps([hit.__dict__ for hit in hits], ensure_ascii=False, indent=1, default=str))
        return
    if not hits:
        print("Nothing found.")
        return
    strong = strong_count(hits)
    for index, hit in enumerate(hits):
        if index == strong:
            print("  — nothing clearly matches; the closest —" if strong == 0 else "  — weaker matches —")
        where = f" {_place(hit.kind, hit.loc, hit.loc_end)}" if hit.loc is not None else ""
        signal = " ".join(filter(None, [f"z={hit.z:.1f}" if hit.z is not None else "",
                                        f"kw#{hit.keyword_rank}" if hit.keyword_rank else ""]))
        print(f"{index + 1:2d}. [{hit.kind.value}] {hit.path}{where}   ({hit.score:.2f}: {signal})")
        if hit.excerpt:
            print(f"      {hit.excerpt[:200]}")
        if hit.moments:
            print("      also " + ", ".join(_place(hit.kind, loc, end) for loc, end in hit.moments))


def cmd_search(args) -> None:
    choice = _choice()
    store = Store()
    started = time.monotonic()
    with runtime.Server(choice, text_only=True) as server, Embedder(server.url, parallel=1, model=server.model) as embedder:
        ready = time.monotonic()
        probes.ensure(store, embedder)
        searcher = Searcher(store)
        loaded = time.monotonic()
        vector = embedder.query(for_meaning(args.query))
        if searcher.model_tag not in (None, server.model.vector_tag):
            vector = None   # a model switch during this CLI query: words remain usable; never mix embedding spaces
        embedded = time.monotonic()
        kinds = {Kind(k) for k in args.kind} if args.kind else None
        hits = searcher.search(args.query, vector, kinds=kinds, limit=args.limit)
        done = time.monotonic()
    _print_hits(hits, args.json)
    if not args.json:
        print(f"\n{len(searcher.matrix)} vectors · model {1000 * (ready - started):.0f} ms · load "
              f"{1000 * (loaded - ready):.0f} ms · query {1000 * (embedded - loaded):.0f} ms · rank "
              f"{1000 * (done - embedded):.0f} ms")


def cmd_similar(args) -> None:
    """Query by example: a picture, a recording or a document finds what's like it, whatever kind it is."""
    from . import rules, searchtext
    from .embedder import Prompts
    from .extract import docs, images, media

    choice = _choice()
    path = os.path.abspath(args.file)
    kind = rules.classify(path)
    store = Store()
    with runtime.Server(choice) as server, Embedder(server.url, model=server.model) as embedder:
        probes.ensure(store, embedder)
        searcher = Searcher(store)
        if kind in (Kind.IMAGE, Kind.SCREENSHOT):
            vector = embedder.images([images.load(path, 0).image], 280)[0]
        elif kind is Kind.AUDIO:
            window = next(media.sound(path), None)   # its first 30 s with sound
            if window is None:
                sys.exit("it's silent")
            vector = embedder.audio([window.wav])[0]
        elif kind is Kind.VIDEO:
            with media.FrameReader(path) as reader:
                middle = reader.duration / 2
                frames = reader.frames(middle, reader.duration, spacing=1.0)[:1] or reader.frames(0, middle, 1.0)[:1]
            if not frames:
                sys.exit("no frames in it")
            vector = embedder.images([frames[0].image], 280)[0]
        elif kind is Kind.DOC:
            text, title = docs.read(path)
            vector = embedder.texts([Prompts.document(searchtext.chunks(text)[0], title)])[0]
        else:
            sys.exit(f"can't search by {kind or 'this kind of file'} yet")
    if isinstance(vector, Exception):
        sys.exit(f"the model couldn't read it: {vector}")
    if vector is None:
        sys.exit("The selected model is text-only. Choose a multimodal model in Settings to search by pictures or sound.")
    kinds = {Kind(k) for k in args.kind} if args.kind else None
    hits = searcher.search("", vector, kinds=kinds, limit=args.limit, exclude={path}, z_gate=float("-inf"))
    _print_hits(hits, args.json)


def _expected(hit, base: Path, expect: str) -> bool:
    """"photos/a.jpg" matches the file; "talk.mp3@30-55" only a hit whose moment overlaps 30–55 s."""
    path, _, moment = expect.partition("@")
    if paths.fold(hit.path) != paths.fold(str(base / path)):
        return False
    if not moment:
        return True
    start, end = (float(x) for x in moment.split("-"))
    return hit.loc is not None and hit.loc <= end and (hit.loc_end if hit.loc_end is not None else hit.loc) >= start


def cmd_eval(args) -> None:
    """Runs an eval file ({"base": folder, "queries": [{"q": …, "expect": [relative paths]}]}): recall@1, @5, MRR."""
    spec = json.loads(Path(args.file).read_text(encoding="utf-8"))
    base = Path(os.path.expanduser(args.base or spec["base"]))
    choice = _choice()
    store = Store()
    results, strong = [], []
    with runtime.Server(choice, text_only=True) as server, Embedder(server.url, parallel=1, model=server.model) as embedder:
        probes.ensure(store, embedder)
        searcher = Searcher(store)
        for query in spec["queries"]:
            hits = searcher.search(query["q"], embedder.query(for_meaning(query["q"])), limit=10)
            ranks = [i + 1 for i, h in enumerate(hits) if any(_expected(h, base, e) for e in query["expect"])]
            results.append((query, ranks[0] if ranks else None))
            strong.append(strong_count(hits))
    total = len(results)
    at1 = sum(1 for _, r in results if r == 1)
    at5 = sum(1 for _, r in results if r and r <= 5)
    mrr = sum(1 / r for _, r in results if r) / max(total, 1)
    for query, rank in results:
        if rank != 1:
            print(f"  {'miss' if rank is None else f'#{rank}':5s} {query['q']}")
    print(f"{total} queries: R@1 {at1 / total:.2f} ({at1}) · R@5 {at5 / total:.2f} ({at5}) · MRR {mrr:.3f} · "
          f"{sum(strong) / total:.1f} strong results a query")


def cmd_bench(args) -> None:
    """How fast this PC embeds, on the configured runtime (or --backend)."""
    from PIL import Image

    choice = runtime.choose(args.backend) if args.backend else _choice()
    sample = Path(__file__).resolve().parents[2] / "tests" / "reference" / "photo.jpg"
    picture = Image.open(sample).convert("RGB") if sample.exists() else Image.new("RGB", (1024, 768), "gray")
    with runtime.Server(choice) as server, Embedder(server.url, parallel=server.slots, model=server.model) as embedder:
        embedder.texts(["warm up"])
        embedder.images([picture], 70)

        def timed(label: str, work, count: int = 1) -> None:
            started = time.perf_counter()
            work()
            seconds = time.perf_counter() - started
            print(f"  {label:28s} {seconds * 1000 / count:7.0f} ms each")

        print(f"{choice.describe()} ({server.slots} slots):")
        timed("query", lambda: embedder.query("a dog on the beach"))
        passage = "The committee reviewed the budget proposal and agreed on the timeline. " * 25
        timed("passage (~450 tokens) ×16", lambda: embedder.texts([passage] * 16), 16)
        for budget in (70, 140, 280):
            timed(f"picture @{budget} ×8", lambda b=budget: embedder.images([picture] * 8, b), 8)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    migrate.run()   # before anything opens a file in the data folder
    parser = argparse.ArgumentParser(prog="dowse", description="Search your files by what's in them.")
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="download the model and the llama.cpp runtime for this PC")
    setup.add_argument("--backend", default="auto", choices=["auto", *runtime.BACKENDS])
    setup.add_argument("--device", help="a GPU by llama.cpp's name for it (Vulkan1, CUDA0)")
    setup.set_defaults(run=cmd_setup)

    status = commands.add_parser("status", help="what's set up and what's indexed")
    status.add_argument("-v", "--verbose", action="store_true", help="list files that failed or were skipped")
    status.set_defaults(run=cmd_status)

    index = commands.add_parser("index", help="index folders (remembered; no folders: the remembered ones)")
    index.add_argument("folders", nargs="*")
    index.add_argument("--limit", type=int, help="read at most this many files")
    index.add_argument("--retry", action="store_true", help="try files that failed before again")
    index.set_defaults(run=cmd_index)

    for name, function, help_text in [("search", cmd_search, "search by meaning and words"),
                                      ("similar", cmd_similar, "find what's like a picture, recording or document")]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("file" if name == "similar" else "query")
        command.add_argument("-k", "--kind", action="append", choices=[k.value for k in Kind])
        command.add_argument("-n", "--limit", type=int, default=10)
        command.add_argument("--json", action="store_true")
        command.set_defaults(run=function)

    watch = commands.add_parser("watch", help="keep the index in step with the folders, live (Ctrl+C stops)")
    watch.set_defaults(run=cmd_watch)

    add = commands.add_parser("add", help="search these files or folders too (files from anywhere)")
    add.add_argument("paths", nargs="+", metavar="PATH")
    add.set_defaults(run=cmd_add)
    remove = commands.add_parser("remove", help="stop searching these files or folders (they aren't touched)")
    remove.add_argument("paths", nargs="+", metavar="PATH")
    remove.set_defaults(run=cmd_remove)
    for op in ("activate", "deactivate"):
        active = commands.add_parser(op, help=f"{op} search for files or folders without removing them")
        active.add_argument("paths", nargs="+", metavar="PATH")
        active.set_defaults(run=lambda args, op=op: _change(op, args.paths))
    files = commands.add_parser("files", help="what Dowse searches, file by file, and how each one is doing")
    files.add_argument("view", nargs="?", default="all",
                       help="all, added (one by one), attention (couldn't be read), removed, or a watched folder")
    files.add_argument("--json", action="store_true")
    files.set_defaults(run=cmd_files)

    reset = commands.add_parser("reset", help="delete the index (not your files)")
    reset.set_defaults(run=cmd_reset)

    evaluate = commands.add_parser("eval", help="measure ranking on an eval file")
    evaluate.add_argument("file")
    evaluate.add_argument("--base", help="the folder the expected paths are relative to")
    evaluate.set_defaults(run=cmd_eval)

    bench = commands.add_parser("bench", help="how fast this PC embeds")
    bench.add_argument("--backend", choices=list(runtime.BACKENDS))
    bench.set_defaults(run=cmd_bench)

    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
