"""More like this, and what was gone back to: neighbours from the vectors already in the index (a file, or a place in
it), a file the index doesn't hold read for it, a dropped copy standing for its original, uploads, and the history of
opened files with the searches that found them."""

import http.client
import json
import os
import types
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pytest

import test_desktop
from dowse import history as history_module
from dowse.desktop import launch, shell, system
from dowse.desktop.server import Server
from dowse.embedder import DIM
from dowse.history import History
from dowse.rules import Kind
from dowse.searcher import LIKE_VECTORS, Searcher
from dowse.store import Modality, NewSegment, Segment, Store
from test_desktop import request
from test_index import FakeEmbedder, unit

app = test_desktop.app


def near(topic: np.ndarray, seed: int, noise: float = 0.25) -> np.ndarray:
    vector = topic + noise * np.random.default_rng(seed).standard_normal(DIM).astype(np.float32) / np.sqrt(DIM)
    return vector / np.linalg.norm(vector)


@pytest.fixture
def library(tmp_path):
    """Two subjects (a and b) among 30 unrelated pictures: a photo of a; a video that shows b, then a, while b is
    heard; a PDF whose page 1 is about b and page 2 about a; notes about b."""
    a, b = unit(1), unit(2)
    store = Store(tmp_path / "index.sqlite")
    paths = {}

    def add(name: str, kind: Kind, segments: list[NewSegment]) -> None:
        paths[name] = str(tmp_path / name)
        file = store.add_file(paths[name], kind, 100, 1_700_000_000)
        store.add_segments(file, segments)
        store.finish(file, "done")

    add("photo.png", Kind.IMAGE, [NewSegment(Segment.IMAGE, Modality.IMAGE, near(a, 10))])
    add("trip.mp4", Kind.VIDEO, [NewSegment(Segment.FRAME, Modality.IMAGE, near(b, 11), 0.0),
                                 NewSegment(Segment.FRAME, Modality.IMAGE, near(a, 12), 10.0),
                                 NewSegment(Segment.AUDIO, Modality.AUDIO, near(b, 13), 0.0, 30.0)])
    add("report.pdf", Kind.PDF, [NewSegment(Segment.PAGE, Modality.TEXT, near(b, 14), 1.0),
                                 NewSegment(Segment.PAGE, Modality.TEXT, near(a, 15), 2.0)])
    add("notes.txt", Kind.DOC, [NewSegment(Segment.CHUNK, Modality.TEXT, near(b, 16), 0.0)])
    for i in range(30):
        add(f"other-{i}.png", Kind.IMAGE, [NewSegment(Segment.IMAGE, Modality.IMAGE, unit(100 + i))])
    yield Searcher(store), paths
    store.close()


def test_a_file_finds_what_is_like_some_part_of_it_and_never_itself(library):
    searcher, paths = library
    hits = searcher.like(searcher.vectors(searcher.rows_of(paths["photo.png"])), limit=5, exclude={paths["photo.png"]})
    assert paths["photo.png"] not in [h.path for h in hits]
    first = {h.path: h for h in hits[:2]}
    assert set(first) == {paths["trip.mp4"], paths["report.pdf"]}   # each has a part about a
    assert first[paths["trip.mp4"]].loc == 10.0 and first[paths["report.pdf"]].loc == 2.0   # at that part
    assert all(h.clear for h in hits[:2]) and not any(h.clear for h in hits[2:])


def test_a_place_in_a_file_stands_for_itself(library):
    searcher, paths = library
    page = searcher.rows_of(paths["report.pdf"], 1)
    assert len(page) == 1 and len(searcher.rows_of(paths["report.pdf"])) == 2
    hits = searcher.like(searcher.vectors(page), limit=3, exclude={paths["report.pdf"]})
    assert {h.path for h in hits[:2]} == {paths["notes.txt"], paths["trip.mp4"]}   # both about b, page 1's subject

    def kinds_at(seconds):
        rows = searcher.rows_of(paths["trip.mp4"], seconds)
        found = searcher.store.db.execute(f"SELECT kind, loc FROM segments WHERE id IN "
                                          f"({','.join(str(int(searcher.row_segment[r])) for r in rows)})")
        return sorted(found)

    assert kinds_at(12) == [("audio", 0.0), ("frame", 10.0)]   # the frame showing at 0:12, the sound around it
    assert kinds_at(5) == [("audio", 0.0), ("frame", 0.0)]
    assert kinds_at(99) == [("frame", 10.0)]   # past the sound: the last frame is still what's shown
    assert len(searcher.rows_of(paths["notes.txt"], 7)) == 1   # no passage 7: the whole file
    assert len(searcher.rows_of(str(Path(paths["notes.txt"]).with_name("gone.txt")))) == 0


def test_a_long_file_is_searched_with_an_even_spread_of_its_vectors(library):
    searcher, _ = library
    rows = np.arange(len(searcher.matrix))
    assert len(rows) > LIKE_VECTORS
    picked = searcher.vectors(rows)
    assert picked.shape == (LIKE_VECTORS, DIM) and picked.dtype == np.float32
    assert np.allclose(picked[0], searcher.matrix[0]) and np.allclose(picked[-1], searcher.matrix[-1])


# Through the app

def similar(running, body):
    status, _, raw = request(running.server, "POST", "/api/similar", body)
    return status, json.loads(raw)


def drop(running, name: str, data: bytes, query: str = "") -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", running.server.port, timeout=10)
    connection.request("POST", f"/api/similar/file?name={quote(name)}{query}", body=data,
                       headers={"Host": f"127.0.0.1:{running.server.port}", "Cookie": f"dowse={running.server.token}",
                                "Content-Type": "application/octet-stream"})
    response = connection.getresponse()
    return response.status, json.loads(response.read())


def test_more_like_a_file_of_the_index(app):
    running, folder, elsewhere = app
    cake = str(folder / "notes" / "cake.txt")
    status, answer = similar(running, {"path": cake})
    assert status == 200 and answer["like"] == {"path": cake, "name": "cake.txt", "kind": "doc", "loc": None,
                                                "place": None, "indexed": True}
    names = [Path(h["path"]).name for h in answer["hits"]]
    assert "cake.txt" not in names and names[0] == "recipe.txt"   # the same first word: the same stand-in vector
    status, answer = similar(running, {"path": cake, "kinds": ["image"]})
    assert status == 200 and [h["kind"] for h in answer["hits"]] == ["image"]
    assert similar(running, {"path": str(folder / "gone.txt")})[0] == 404


def test_a_dropped_copy_stands_for_the_file_it_copies(app):
    running, folder, _ = app
    cake = folder / "notes" / "cake.txt"
    status, answer = drop(running, "copy of cake.txt", cake.read_bytes())
    assert status == 200 and answer["like"]["path"] == str(cake) and answer["like"]["indexed"]
    assert str(cake) not in [h["path"] for h in answer["hits"]]
    dropped = Path(os.environ["DOWSE_HOME"]) / "Index" / "Dropped"
    assert not any(dropped.iterdir())   # the copy is gone once answered


def test_a_dropped_file_the_index_does_not_hold_is_read_for_it(app, monkeypatch):
    running, folder, _ = app
    monkeypatch.setattr(running.engine, "choice", types.SimpleNamespace(device=None))
    fake = FakeEmbedder()
    monkeypatch.setattr(running.engine, "_example_model", lambda: fake)
    status, answer = drop(running, "dessert.txt", b"banana split with cream")
    assert status == 200, answer
    assert answer["like"]["path"] is None and not answer["like"]["indexed"] and answer["like"]["name"] == "dessert.txt"
    assert {Path(h["path"]).name for h in answer["hits"][:2]} == {"cake.txt", "recipe.txt"}
    assert fake.embedded == 1
    assert drop(running, "dessert.txt", b"banana split with cream")[0] == 200 and fake.embedded == 1   # kept
    status, answer = drop(running, "thing.xyz", b"?")
    assert status == 422 and "doesn't read" in answer["error"]


def test_uploads_keep_only_their_own_name_and_are_deleted(tmp_path):
    server = Server(static=tmp_path, uploads=tmp_path / "up")
    seen = {}

    @server.route("POST", "/api/take")
    def take(req):
        seen["path"], seen["data"] = req.upload.path, req.upload.path.read_bytes()
        return {"digest": req.upload.digest}

    server.start()
    try:
        request(server, "GET", f"/api/session?t={server.token}", cookie=None)
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        connection.request("POST", "/api/take?name=..%5C..%5Cevil.txt", body=b"hello",
                           headers={"Host": f"127.0.0.1:{server.port}", "Cookie": f"dowse={server.token}",
                                    "Content-Type": "application/octet-stream"})
        response = connection.getresponse()
        assert response.status == 200 and len(json.loads(response.read())["digest"]) == 64
        assert seen["path"].name == "evil.txt" and seen["path"].parent.parent == tmp_path / "up"
        assert seen["data"] == b"hello" and not seen["path"].exists()
    finally:
        server.stop()


def test_find_similar_from_explorer_and_the_command_line(tmp_path):
    target = str(tmp_path / "a.jpg")
    assert launch.message(["--like", target]) == ({"op": "like", "paths": [target]}, False)
    commands = [values[""] for key, values in shell.wanted().items() if key.endswith(r"2similar\command")]
    assert len(commands) == 1 and commands[0].endswith('--like "%1"')


# History

def test_history_keeps_what_was_gone_back_to_and_what_found_it(tmp_path):
    kept = History(tmp_path / "history.json")
    kept.opened("C:/a.pdf", "electricity bill March", now=1)
    kept.opened("C:/b.pdf", "Électricité  facture", now=2)
    kept.opened("C:/c.jpg", None, now=3)   # opened from Explore: no search
    kept.opened("C:/a.pdf", "electricity bill march", now=4)   # the same words for the same file: moved up
    again = History(tmp_path / "history.json")   # read back from disk
    assert again.recent() == ["C:/a.pdf", "C:/c.jpg", "C:/b.pdf"]
    assert again.recent(keep=lambda p: p != "C:/c.jpg") == ["C:/a.pdf", "C:/b.pdf"]
    assert [q["query"] for q in again.queries("ELEC")] == ["electricity bill march"]
    assert [q["path"] for q in again.queries("élec")] == ["C:/b.pdf"]   # spacing aside, as written
    exact = again.queries("electricity bill March")
    assert exact[0]["exact"] and len(exact) == 1
    assert again.queries("rent") == []
    again.forget(keep=lambda p: p == "C:/b.pdf")
    assert History(tmp_path / "history.json").recent() == ["C:/b.pdf"]
    again.forget()
    assert not (tmp_path / "history.json").exists() and again.recent() == []
    (tmp_path / "history.json").write_text("{not json")
    assert History(tmp_path / "history.json").recent() == []


def test_history_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(history_module, "MOST", 3)
    kept = History(tmp_path / "history.json")
    for i in range(5):
        kept.opened(f"C:/{i}.txt", f"query {i}", now=i)
    assert kept.recent() == ["C:/4.txt", "C:/3.txt", "C:/2.txt"]


def test_history_through_the_app(app, monkeypatch):
    running, folder, elsewhere = app
    monkeypatch.setattr(system, "open_file", lambda *args: None)
    monkeypatch.setattr(system, "reveal", lambda *args: None)
    cake, rent = str(folder / "notes" / "cake.txt"), str(folder / "notes" / "rent.txt")

    def history(q=""):
        status, _, raw = request(running.server, "GET", f"/api/history?q={q}")
        assert status == 200
        return json.loads(raw)

    assert history() == {"recent": [], "queries": []}
    assert request(running.server, "POST", "/api/open", {"path": cake, "loc": None, "q": "banana bread"})[0] == 200
    assert request(running.server, "POST", "/api/reveal", {"path": rent})[0] == 200
    answer = history()
    assert [h["path"] for h in answer["recent"]] == [rent, cake] and answer["recent"][1]["name"] == "cake.txt"
    suggested = history("ban")["queries"]
    assert [(q["query"], q["file"]["path"], q["exact"]) for q in suggested] == [("banana bread", cake, False)]

    running.active_paths([cake], False)   # switched off: not shown, and not remembered while off
    assert [h["path"] for h in history()["recent"]] == [rent] and history("ban")["queries"] == []
    request(running.server, "POST", "/api/reveal", {"path": cake, "q": "walnuts"})
    running.active_paths([cake], True)
    assert history("wal")["queries"] == []

    assert request(running.server, "POST", "/api/history/clear", {})[0] == 200
    assert history() == {"recent": [], "queries": []}
    request(running.server, "POST", "/api/open", {"path": cake, "q": "banana"})
    status, _, raw = request(running.server, "POST", "/api/settings", {"history": False})
    assert status == 200 and json.loads(raw)["state"]["settings"]["history"] is False
    assert not history_module.file().exists()   # off: what was kept goes too
    request(running.server, "POST", "/api/open", {"path": cake, "q": "banana"})
    assert history() == {"recent": [], "queries": []} and not history_module.file().exists()


def test_find_similar_from_explorer_opens_search_with_the_file(app):
    running, folder, _ = app
    shown = []
    running.native = types.SimpleNamespace(show_main=lambda route, **kwargs: shown.append(route))
    running.request({"op": "like", "paths": [str(folder / "IMG_1.png")]})
    assert len(shown) == 1 and shown[0].startswith("/search?like=") and "IMG_1.png" in shown[0]
