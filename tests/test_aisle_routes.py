"""What the Aisles view asks of the app: an idea's files (an aisle Dowse grouped, before it's kept), and a file taken
out of an aisle, added to it, and put back, each saved at once, and undone by saving the aisle as it was."""

import json

import pytest

import test_desktop
from dowse.desktop.app import App
from dowse.indexer import Indexer
from dowse.selection import Selection
from dowse.store import Store
from test_desktop import request
from test_index import FakeEmbedder
from test_selection import j, photo, touch

app = test_desktop.app


def get(running, path):
    status, _, raw = request(running.server, "GET", path)
    assert status == 200, raw
    return json.loads(raw)


@pytest.fixture
def library(tmp_path, monkeypatch):
    """The app over a library with groups in it: photos of two subjects, and notes about two things."""
    monkeypatch.setenv("DOWSE_HOME", str(tmp_path / "home"))
    folder = tmp_path / "files"
    for i in range(6):
        photo(folder / "snow" / f"IMG_{i}.png", 7)          # (a picture's stand-in vector: its pixel)
        photo(folder / "beach" / f"IMG_{i}.png", 90)
        touch(folder / "bills" / f"bill {i}.txt", f"invoice number {i}".encode())
        touch(folder / "recipes" / f"recipe {i}.txt", f"pasta with sauce {i}".encode())
    chosen = Selection((str(folder),))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "settings.json").write_text(json.dumps(chosen.to_settings({"hotkey": "Alt+Shift+Space"})))
    store = Store(tmp_path / "home" / "Index" / "index.sqlite")
    Indexer(store, workers=2).sync(chosen)
    Indexer(store, workers=2).run(FakeEmbedder())
    store.close()
    running = App()
    running.start(announce=True)
    request(running.server, "GET", f"/api/session?t={running.server.token}", cookie=None)
    yield running
    running.stop()
    running.server.stop()


def test_an_idea_lists_its_files_before_it_is_kept(library):
    ideas = get(library, "/api/aisles")["suggestions"]
    assert ideas
    listed = []
    for idea in ideas:
        files = get(library, f"/api/aisles/members?id={idea['id']}")
        assert len(files["members"]) == idea["count"] and files["near"] == [] and files["excluded"] == []
        listed += [m["path"] for m in files["members"]]
    assert len(listed) == len(set(listed)) == 24   # every file in one idea (all of them alike within their group)
    assert get(library, "/api/aisles/members?id=nothing-by-that-name")["members"] == []


def test_take_out_add_and_undo_through_saving(app):
    running, folder, elsewhere = app
    rent, cake = j(folder, "notes", "rent.txt"), j(folder, "notes", "cake.txt")
    before = {"name": "Housing", "about": "leases and rent", "pinned": [rent], "excluded": [], "kinds": []}
    saved = json.loads(request(running.server, "POST", "/api/aisles/save", before)[2])
    members = lambda: [m["path"] for m in get(running, f"/api/aisles/members?id={saved['id']}")["members"]]   # noqa: E731
    assert members() == [rent]

    taken = {**saved, "pinned": [], "excluded": [rent]}
    request(running.server, "POST", "/api/aisles/save", taken)
    listing = get(running, f"/api/aisles/members?id={saved['id']}")
    assert rent not in [m["path"] for m in listing["members"]] and [m["path"] for m in listing["excluded"]] == [rent]

    request(running.server, "POST", "/api/aisles/save", {**saved, "pinned": [rent, cake], "excluded": []})
    assert set(members()) == {rent, cake}
    pinned = {m["path"]: m["pinned"] for m in get(running, f"/api/aisles/members?id={saved['id']}")["members"]}
    assert pinned == {rent: True, cake: True}

    request(running.server, "POST", "/api/aisles/save", saved)   # Undo: the aisle as it was
    assert members() == [rent]
    assert [a["count"] for a in get(running, "/api/aisles")["aisles"]] == [1]
