"""What the user chose to search: the rules of a selection, how crawling and indexing follow it (also while it
changes), and the Library's lists of it."""

import os
import sys
import threading

import pytest
from PIL import Image

from dowse import catalog, crawler
from dowse.indexer import Indexer
from dowse.selection import Selection, Source
from dowse.service import IndexService
from dowse.store import Store
from test_index import FakeEmbedder
from test_watch import wait_for


def touch(path, data: bytes = b"x") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def photo(path, shade: int = 7) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), (shade, 0, 0)).save(path)
    return str(path)


def j(*parts) -> str:
    return os.path.join(*map(str, parts))


# The rules

def test_the_most_specific_choice_wins(tmp_path):
    home = j(tmp_path, "home")
    chosen = Selection(folders=(home,), files=(j(home, "old", "keep.pdf"), j(tmp_path, "elsewhere", "a.pdf")),
                       removed=(j(home, "old"), j(home, "photos", "private.jpg")))
    assert chosen.decide(j(home, "photos", "IMG_1.jpg")) is Source.FOLDER
    assert chosen.decide(j(home, "photos", "private.jpg")) is None          # removed inside a watched folder
    assert chosen.decide(j(home, "old", "x.pdf")) is None                   # in a removed folder
    assert chosen.decide(j(home, "old", "keep.pdf")) is Source.NAMED        # added by name inside it: in
    assert chosen.decide(j(tmp_path, "elsewhere", "a.pdf")) is Source.NAMED
    assert chosen.decide(j(tmp_path, "elsewhere", "b.pdf")) is None
    assert chosen.decide(j(tmp_path, "homework", "a.pdf")) is None          # a prefix isn't a parent
    assert chosen.watched(j(home, "old", "x.pdf")) and not chosen.watched(j(tmp_path, "elsewhere", "a.pdf"))
    if sys.platform == "win32":
        assert chosen.decide(j(home, "PHOTOS", "Private.JPG")) is None      # Windows paths ignore case


def test_settings_round_trip_and_the_old_name_for_removals():
    old = {"folders": ["C:\\a"], "excluded": ["C:\\a\\b"], "hotkey": "Alt+Space"}
    chosen = Selection.from_settings(old)
    assert chosen.removed == ("C:\\a\\b",)
    saved = chosen.to_settings(old)
    assert "excluded" not in saved and saved["removed"] == ["C:\\a\\b"] and saved["hotkey"] == "Alt+Space"


def test_adding(tmp_path):
    home = tmp_path / "home"
    plain = touch(home / "docs" / "a.pdf")
    in_project = touch(home / "code" / "notes.md")
    touch(home / "code" / "package.json")
    outside = touch(tmp_path / "elsewhere" / "b.pdf")
    chosen = Selection((str(home),), removed=(j(home, "docs"),))

    assert Selection((str(home),), removed=(plain,)).add([plain]) == Selection((str(home),))   # removal undone
    assert chosen.add([plain]) == Selection((str(home),), (plain,), (j(home, "docs"),))      # just that file, by name
    after = chosen.add([outside, in_project])
    assert after.files == tuple(sorted([outside, in_project]))              # a code project's file: by name
    assert after.decide(in_project) is Source.NAMED
    assert after.add([outside]) == after                                    # twice is once
    assert chosen.add([j(home, "docs")]).removed == ()                      # a covered folder: its removal goes
    assert chosen.add([j(home, "docs")]).folders == (str(home),)            # …and it isn't watched twice

    inner = Selection((j(home, "docs"), j(home, "code")), files=(outside,))
    assert inner.add([str(home)]).folders == (str(home),)                   # nested folders: the parent covers them
    assert inner.add([str(home)]).files == (outside,)


def test_removing(tmp_path):
    home, other = tmp_path / "home", tmp_path / "other"
    named_inside = touch(home / "x" / "keep.pdf")
    named_outside = touch(other / "b.pdf")
    plain = touch(home / "docs" / "a.pdf")
    chosen = Selection((str(home), str(other / "sub")), files=(named_inside, named_outside),
                       removed=(j(home, "x"), j(other, "sub", "y")))

    one = chosen.remove([plain])
    assert plain in one.removed and one.decide(plain) is None
    assert chosen.remove([named_outside]).files == (named_inside,)          # added by name: just dropped
    assert chosen.remove([named_outside]).removed == chosen.removed         # (nothing covers it: no removal)
    away = chosen.remove([str(home)])                                       # a watched folder: unwatched…
    assert away.folders == (j(other, "sub"),)
    assert away.files == (named_outside,)                                   # …with the files added inside it
    assert away.removed == (j(other, "sub", "y"),)                          # its removals mean nothing now
    assert chosen.remove([plain]).remove([plain]) == chosen.remove([plain])


# Crawling what's chosen

def test_crawling_folders_and_files_added_by_name(tmp_path):
    home = tmp_path / "home"
    touch(home / "docs" / "a.pdf")
    removed = touch(home / "docs" / "private.pdf")
    touch(home / "old" / "x.pdf")
    rescued = touch(home / "old" / "keep.pdf")
    in_project = touch(home / "code" / "notes.md")
    touch(home / "code" / "package.json")
    hidden = touch(home / ".config" / "manual.pdf")
    outside = touch(tmp_path / "elsewhere" / "b.pdf")
    key = touch(tmp_path / "elsewhere" / "id_ed25519")
    binary = touch(tmp_path / "elsewhere" / "tool.exe")
    gone = j(tmp_path, "elsewhere", "gone.pdf")
    chosen = Selection((str(home),), files=(rescued, in_project, hidden, outside, key, binary, gone),
                       removed=(removed, j(home, "old")))

    found = crawler.crawl(chosen)
    paths = sorted(os.path.relpath(f.path, tmp_path).replace(os.sep, "/") for f in found.files)
    assert paths == ["elsewhere/b.pdf", "home/.config/manual.pdf", "home/code/notes.md", "home/docs/a.pdf",
                     "home/old/keep.pdf"]
    assert found.refused == {key: "key or password file", binary: "a type Dowse can't read"}
    assert found.skipped["removed by you"] == 2

    assert isinstance(crawler.check(in_project, chosen), crawler.Found)     # added by name: the rules don't apply
    assert crawler.check(removed, chosen) == "not chosen"
    assert crawler.check(gone, chosen) is None
    within = crawler.crawl(chosen, within=j(home, "old"))
    assert [f.path for f in within.files] == [rescued]


def test_sync_follows_the_selection(tmp_path):
    """What stops being chosen leaves the index; what comes back is crawled again."""
    home = tmp_path / "home"
    photo(home / "a.png", 1)
    photo(home / "trip" / "b.png", 2)
    outside = photo(tmp_path / "elsewhere" / "c.png", 3)
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, workers=2)

    def names() -> list[str]:
        return sorted(os.path.basename(f.path) for f in store.files())

    chosen = Selection((str(home),))
    indexer.sync(chosen)
    assert names() == ["a.png", "b.png"]
    chosen = chosen.add([outside]).remove([j(home, "trip")])
    summary = indexer.sync(chosen)
    assert names() == ["a.png", "c.png"] and (summary.added, summary.removed) == (1, 1)
    summary = indexer.update([j(home, "trip"), outside], chosen.add([j(home, "trip")]).remove([outside]))
    assert names() == ["a.png", "b.png"] and (summary.added, summary.removed) == (1, 1)


@pytest.mark.skipif(sys.platform not in ("win32", "darwin", "linux"), reason="no folder watcher on this system")
def test_the_service_applies_a_new_selection_live(tmp_path):
    home = tmp_path / "home"
    photo(home / "a.png", 1)
    photo(home / "trip" / "b.png", 2)
    outside = photo(tmp_path / "elsewhere" / "c.png", 3)
    store = Store(tmp_path / "index.sqlite")
    events = []
    service = IndexService(store, Selection((str(home),)), Indexer(store).options,
                           lambda: (FakeEmbedder(), lambda: None), quiet=0.2, idle=0.5, on_event=events.append)
    stop = threading.Event()
    thread = threading.Thread(target=service.run, args=(stop,), daemon=True)
    thread.start()

    def states() -> dict[str, str]:
        reader = Store(store.path)
        try:
            return {os.path.basename(f.path): f.state for f in reader.files()}
        finally:
            reader.close()

    try:
        assert service.ready.wait(10)
        wait_for(lambda: states() == {"a.png": "done", "b.png": "done"})
        service.set_selection(Selection((str(home),), (outside,), (j(home, "trip"),)))
        wait_for(lambda: states() == {"a.png": "done", "c.png": "done"})
        chosen = [e for e in events if e.kind == "chosen"]
        assert chosen and (chosen[0].summary.added, chosen[0].summary.removed) == (1, 1)
        photo(tmp_path / "elsewhere" / "c.png", 9)                         # an added file is watched too
        wait_for(lambda: any(e.kind == "change" and "1 changed" in e.message for e in events))
        service.read_again([j(home, "a.png")])
        wait_for(lambda: any(e.kind == "change" and "to read again" in e.message for e in events))
        wait_for(lambda: states()["a.png"] == "done")
    finally:
        stop.set()
        thread.join(10)


# The Library's lists

def test_the_catalog_lists_and_counts(tmp_path):
    home = tmp_path / "home"
    photo(home / "a.png", 1)
    photo(home / "trip" / "b.png", 2)
    removed = photo(home / "trip" / "private.png", 3)
    outside = photo(tmp_path / "elsewhere" / "c.png", 4)
    key = touch(tmp_path / "elsewhere" / "server.pem")
    waiting = photo(tmp_path / "elsewhere" / "later.png", 5)
    gone = j(tmp_path, "elsewhere", "gone.png")
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, workers=2)
    chosen = Selection((str(home),), (outside, key, gone), (removed,))
    indexer.sync(chosen)
    indexer.run(FakeEmbedder())
    with store.transaction():
        store.finish(next(f.id for f in store.files() if f.path.endswith("b.png")), "failed", "the model refused it")
    chosen = Selection((str(home),), (outside, key, gone, waiting), (removed,))   # added since the last crawl

    def listed(view: str) -> dict[str, str]:
        return {e.name: e.status_text for e in catalog.entries(store, chosen, view)}

    assert listed(catalog.ALL) == {"a.png": "Searchable", "b.png": "Couldn't read: the model refused it",
                                   "c.png": "Searchable"}
    assert listed(str(home / "trip")) == {"b.png": "Couldn't read: the model refused it"}
    assert listed(catalog.NAMED) == {"c.png": "Searchable", "server.pem": "Can't be searched: key or password file",
                                     "gone.png": "Not found: moved or deleted", "later.png": "Waiting to be read"}
    assert set(listed(catalog.ATTENTION)) == {"b.png", "server.pem", "gone.png"}
    assert listed(catalog.REMOVED) == {"private.png": "Removed by you"}
    assert all(e.named for e in catalog.entries(store, chosen, catalog.ALL) if e.name == "c.png")
    assert catalog.counts(store, chosen) == {catalog.ALL: 3, catalog.NAMED: 4, catalog.REMOVED: 1,
                                             catalog.ATTENTION: 3, catalog.FOLDERS: 1, catalog.INACTIVE: 0, str(home): 2}
    assert catalog.refusals([key, outside, j(home, "a.png")], chosen) == {key: "key or password file"}
    newer = photo(tmp_path / "elsewhere" / "d.png", 6)
    after, refused = catalog.add(Selection((str(home),)), [key, newer])
    assert refused == {key: "key or password file"} and after.files == (newer,)   # never readable: not kept


# Searching in one folder

def test_searching_in_one_folder(tmp_path):
    """Only that folder's files, whatever the spelling of its path, and found even when far more matches are
    elsewhere (the words' best 1,000 could all be elsewhere)."""
    from dowse.searcher import Searcher
    from test_index import word_vector

    home = tmp_path / "home"
    for i in range(1200):
        touch(home / "elsewhere" / f"note {i}.txt", b"invoice for the garden shed")
    touch(home / "Taxes" / "2026" / "invoice.txt", b"invoice for the accountant")
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, workers=4)
    indexer.sync(Selection((str(home),)))
    indexer.run(FakeEmbedder())
    searcher = Searcher(store)

    def found(within: str | None) -> list[str]:
        return [os.path.relpath(h.path, home) for h in searcher.search("invoice", word_vector("invoice"), limit=5,
                                                                      within=within)]

    assert len(found(None)) == 5
    assert found(j(home, "Taxes")) == [j("Taxes", "2026", "invoice.txt")]
    if sys.platform == "win32":
        assert found(j(home, "TAXES") + "\\") == [j("Taxes", "2026", "invoice.txt")]
    assert found(j(home, "Nothing here")) == []


# File types the user skips

def test_skipping_file_types(tmp_path):
    from dowse import rules
    from dowse.reading import Options
    from dowse.rules import Kind, Skip

    home = tmp_path / "home"
    photo(home / "IMG_1.png", 1)
    photo(home / "Screenshots" / "Screenshot 1.png", 2)
    touch(home / "notes.txt", b"banana bread")
    log = touch(home / "server.log.txt", b"error")
    skip = Skip.of(["screenshot"], [".TXT", " "])
    assert skip == Skip(frozenset({Kind.SCREENSHOT}), frozenset({"txt"}))
    assert Skip.from_settings(skip.to_settings()) == skip
    assert rules.classify(j(home, "Screenshots", "Screenshot 1.png"), skip) == rules.SKIPPED_TYPE
    assert rules.classify(j(home, "IMG_1.png"), skip) is Kind.IMAGE

    store = Store(tmp_path / "index.sqlite")
    chosen = Selection((str(home),))
    Indexer(store).sync(chosen)
    assert len(store.files()) == 4
    Indexer(store, Options(skip=skip)).sync(chosen)                         # skipped since: they leave the index
    assert [os.path.basename(f.path) for f in store.files()] == ["IMG_1.png"]
    Indexer(store, Options(skip=skip)).sync(catalog.add(chosen, [log], skip)[0])   # added by name: your choice wins
    assert sorted(os.path.basename(f.path) for f in store.files()) == ["IMG_1.png", "server.log.txt"]
