"""The desktop app without its windows: the local server (who may talk to it, what it serves), the app's routes over a
real index, a second launch handing its request over, Explore's map, the query model's sleep, the speed estimate, the
power policy, the windows' process protocol, and Explorer's menu."""

import http.client
import json
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from dowse import catalog, explore, modelfiles, runtime, speed
from dowse.desktop import launch, system, winapi
from dowse.desktop.app import App, choice_message
from dowse.desktop.engine import SearchEngine
from dowse.desktop.indexing import Config, Indexing
from dowse.desktop.native import WindowHost
from dowse.desktop.server import Raw, Server, Stream
from dowse.indexer import Indexer
from dowse.reading import Options
from dowse.rules import Kind
from dowse.selection import Selection
from dowse.store import Store, writer_lock
from test_index import FakeEmbedder, word_vector
from test_selection import j, photo, touch

windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def request(server: Server, method: str, path: str, body=None, cookie: str | None = "",
            host: str | None = None, headers: dict | None = None) -> tuple[int, dict, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    sent = {"Host": host or f"127.0.0.1:{server.port}", **(headers or {})}
    if cookie is not None:
        sent["Cookie"] = f"dowse={cookie or server.token}"
    data = json.dumps(body).encode() if body is not None else None
    if data:
        sent["Content-Type"] = "application/json"
    connection.request(method, path, body=data, headers=sent)
    response = connection.getresponse()
    return response.status, dict(response.getheaders()), response.read()


# The server: who may talk to it, and what it serves

def test_the_server_answers_only_its_own_pages(tmp_path):
    (tmp_path / "index.html").write_text("<p>the interface</p>")
    server = Server(static=tmp_path)
    media = tmp_path / "talk.mp3"
    media.write_bytes(bytes(range(256)) * 4)

    @server.route("GET", "/api/echo")
    def echo(req):
        return {"query": req.query}

    @server.route("GET", "/api/file")
    def file(_):
        return Stream(str(media), "audio/mpeg")

    @server.route("GET", "/api/raw")
    def raw(_):
        return Raw(b"RIFF", "image/webp")

    server.start()
    try:
        assert request(server, "GET", "/", cookie=None)[0] == 200                    # the page itself: no secret
        assert request(server, "GET", "/api/echo", cookie=None)[0] == 403             # no session, no answer
        assert request(server, "GET", "/api/echo", cookie="guessed")[0] == 403
        assert request(server, "GET", "/api/echo", host="evil.example:80")[0] == 421  # DNS rebinding
        assert request(server, "GET", "/api/session?t=wrong", cookie=None)[0] == 403
        status, headers, _ = request(server, "GET", f"/api/session?t={server.token}", cookie=None)
        assert status == 200 and "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
        status, _, body = request(server, "GET", "/api/echo?a=1")
        assert status == 200 and json.loads(body) == {"query": {"a": "1"}}
        assert request(server, "GET", "/api/nope")[0] == 404
        status, headers, body = request(server, "GET", "/api/file", headers={"Range": "bytes=100-199"})
        assert status == 206 and headers["Content-Range"] == "bytes 100-199/1024" and body == bytes(range(100, 200))
        assert request(server, "GET", "/api/raw")[2] == b"RIFF"
    finally:
        server.stop()


def test_a_refresh_the_page_makes_itself_does_not_count_as_the_person_using_dowse(tmp_path):
    """Reading files steps aside while the person uses Dowse; the page's own refreshes after indexing (issue #1) must
    not count, or they'd slow the very reading they follow."""
    server = Server(static=tmp_path)
    touches = []
    server.on_request = lambda: touches.append(1)

    @server.route("POST", "/api/search")
    def search(_):
        return {"hits": []}

    server.start()
    try:
        assert request(server, "POST", "/api/search", {"q": "photo"})[0] == 200
        assert len(touches) == 1                                   # the person searched
        assert request(server, "POST", "/api/search", {"q": "photo"}, headers={"X-Dowse-Background": "1"})[0] == 200
        assert len(touches) == 1                                   # the page refreshed by itself
    finally:
        server.stop()


# The app's routes, over a real index

@pytest.fixture
def app(tmp_path, monkeypatch):
    """The app (no windows) in its own home: a folder indexed, a file added from elsewhere, one removed."""
    monkeypatch.setenv("DOWSE_HOME", str(tmp_path / "home"))
    folder = tmp_path / "files"
    photo(folder / "IMG_1.png", 1)
    touch(folder / "notes" / "rent.txt", b"lease terms: the rent is 1,200 euros")
    touch(folder / "notes" / "cake.txt", b"banana bread with walnuts")
    elsewhere = touch(tmp_path / "elsewhere" / "recipe.txt", b"banana cake with chocolate")
    chosen = Selection((str(folder),), (elsewhere,))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "settings.json").write_text(json.dumps(chosen.to_settings({"hotkey": "Alt+Shift+Space"})))
    store = Store(tmp_path / "home" / "Index" / "index.sqlite")
    indexer = Indexer(store, workers=2)
    indexer.sync(chosen)
    indexer.run(FakeEmbedder())
    store.close()
    running = App()
    running.start(announce=True)
    request(running.server, "GET", f"/api/session?t={running.server.token}", cookie=None)
    yield running, folder, elsewhere
    running.stop()
    running.server.stop()


def test_searching_and_listing_through_the_app(app):
    running, folder, elsewhere = app
    status, _, body = request(running.server, "POST", "/api/search", {"q": "banana", "kinds": [], "within": None})
    answer = json.loads(body)
    assert status == 200 and not answer["meaning"]   # no model here: words alone
    assert {Path(h["path"]).name for h in answer["hits"]} == {"cake.txt", "recipe.txt"}
    status, _, body = request(running.server, "POST", "/api/search",
                              {"q": "banana", "kinds": [], "within": str(folder)})
    assert [Path(h["path"]).name for h in json.loads(body)["hits"]] == ["cake.txt"]

    listing = json.loads(request(running.server, "GET", "/api/library?view=named")[2])
    assert [e["name"] for e in listing["entries"]] == ["recipe.txt"] and listing["counts"][catalog.ALL] == 4
    recent = json.loads(request(running.server, "GET", "/api/recent?limit=10")[2])["hits"]
    assert len(recent) == 4
    passage = json.loads(request(running.server, "GET", f"/api/passage?segment=0&q=rent&path={j(folder, 'notes', 'rent.txt')}")[2])
    assert "«rent»" in passage["text"]


def test_switches_hide_files_immediately_and_preserve_their_cache(app):
    running, folder, elsewhere = app
    status, _, body = request(running.server, "POST", "/api/choose", {"op": "deactivate", "paths": [str(folder)]})
    before = json.loads(body)["before"]
    assert status == 200 and running.selection.inactive == (str(folder),)
    assert running.selection.folders == (str(folder),)
    answer = json.loads(request(running.server, "POST", "/api/search", {"q": "banana"})[2])
    assert [h["path"] for h in answer["hits"]] == [elsewhere]  # before the writer can even turn
    recent = json.loads(request(running.server, "GET", "/api/recent")[2])["hits"]
    assert [h["path"] for h in recent] == [elsewhere]
    listing = json.loads(request(running.server, "GET", "/api/library?view=folders")[2])
    assert len(listing["entries"]) == 1 and not listing["entries"][0]["active"]
    listing = json.loads(request(running.server, "GET", "/api/library?view=all")[2])
    assert len(listing["entries"]) == 4 and sum(e["active"] for e in listing["entries"]) == 1
    blocked = json.loads(request(running.server, "POST", "/api/choose",
                               {"op": "activate", "paths": [j(folder, "notes", "cake.txt")]})[2])
    assert not blocked["changed"] and list(blocked["blocked"].values()) == [str(folder)]
    store = Store()
    try:
        vectors = store.vector_count()
        Indexer(store).sync(running.selection)
        assert store.vector_count() == vectors
        running._index_changed()
        assert all(p["path"] == elsewhere for p in json.loads(request(running.server, "GET", "/api/explore")[2])["pieces"])
        assert request(running.server, "POST", "/api/choose", {"op": "restore", "selection": before})[0] == 200
        Indexer(store).sync(running.selection)
        running._index_changed()
        assert store.vector_count() == vectors and not running.selection.inactive
        assert len(json.loads(request(running.server, "POST", "/api/search", {"q": "banana"})[2])["hits"]) == 2
    finally:
        store.close()


def test_removed_root_and_named_file_can_be_added_back_from_the_library(app):
    running, folder, elsewhere = app
    assert request(running.server, "POST", "/api/choose", {"op": "remove", "paths": [str(folder), elsewhere]})[0] == 200
    listing = json.loads(request(running.server, "GET", "/api/library?view=removed")[2])
    assert {e["path"] for e in listing["entries"]} == {str(folder), elsewhere}
    assert not running.selection.folders and not running.selection.files
    assert Path(elsewhere).read_text() == "banana cake with chocolate"
    assert request(running.server, "POST", "/api/choose", {"op": "add", "paths": [str(folder), elsewhere]})[0] == 200
    assert not running.selection.forgotten and running.selection.folders == (str(folder),)


def test_failed_settings_save_does_not_apply_a_search_switch(app, monkeypatch):
    running, folder, _ = app
    before = running.selection
    def failed(_):
        raise OSError("disk full")
    monkeypatch.setattr("dowse.desktop.app.settings.save", failed)
    assert request(running.server, "POST", "/api/choose", {"op": "deactivate", "paths": [str(folder)]})[0] == 500
    assert running.selection == before


def test_cached_inactive_media_never_downloads_an_online_only_original(app, monkeypatch):
    running, folder, _ = app
    running.active_paths([str(folder)], active=False)
    monkeypatch.setattr("dowse.desktop.app.cloud_only", lambda _: True)
    path = j(folder, "IMG_1.png")
    assert request(running.server, "GET", f"/api/picture?path={path}")[0] == 409
    assert request(running.server, "GET", f"/api/media?path={path}")[0] == 409


def test_pictures_and_media_only_for_files_in_the_index(app, tmp_path):
    running, folder, _ = app
    status, headers, body = request(running.server, "GET", f"/api/picture?path={j(folder, 'IMG_1.png')}&px=128")
    assert status == 200 and headers["Content-Type"] == "image/webp" and body[:4] == b"RIFF"
    secret = touch(tmp_path / "private" / "passwords.png", b"x")
    assert request(running.server, "GET", f"/api/picture?path={secret}")[0] == 404
    assert request(running.server, "GET", f"/api/media?path={secret}")[0] == 404
    status, _, body = request(running.server, "GET", f"/api/media?path={j(folder, 'notes', 'rent.txt')}")
    assert status == 200 and body.startswith(b"lease terms")


def controlled_model_job(monkeypatch, running, outcome="done"):
    """A held downloader; activation uses the real HTTP routes, writer lock and SQLite transaction."""
    from dowse.desktop import setup as setup_module
    original = setup_module.SetupJob
    release = threading.Event()
    monkeypatch.setattr(running, "begin", lambda: None)

    class Job(original):
        def _run(self):
            self.on_progress({"stage": "model", "done": 1, "total": 100, "rate": 1, "left": 99})
            release.wait(5)
            if self.cancelled.is_set():
                self.on_failed("Preparation cancelled")
                return
            if outcome == "failed":
                self.on_failed("The downloaded model failed validation")
                return
            try:
                self.activating = True
                self.on_progress({"stage": "activate", "done": 100, "total": 100, "rate": 0, "left": None})
                self.on_done(runtime.Choice("cpu", None))
                self.on_progress({"stage": "done", "done": 100, "total": 100, "rate": 0, "left": None})
            except Exception as error:
                self.on_failed(str(error))

        def cancel(self):
            accepted = super().cancel()
            if accepted:
                release.set()
            return accepted

    monkeypatch.setattr(setup_module, "SetupJob", Job)
    return release


def test_model_catalogue_and_explicit_confirmation(app):
    running, _, _ = app
    catalogue = json.loads(request(running.server, "GET", "/api/models")[2])
    assert catalogue["selected"] == modelfiles.DEFAULT and catalogue["hasIndex"]
    assert [m["multimodal"] for m in catalogue["models"]] == [True, False]
    assert catalogue["models"][1]["bytes"] == 277_852_192
    assert request(running.server, "POST", "/api/models/change", {"model": "arbitrary.gguf", "confirmRebuild": True})[0] == 400
    assert request(running.server, "POST", "/api/models/change", {"model": modelfiles.MODELS[1].id})[0] == 409
    assert request(running.server, "POST", "/api/setup/start", {"model": modelfiles.MODELS[1].id})[0] == 409


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_failed_or_cancelled_model_preparation_preserves_the_index(app, monkeypatch, outcome):
    running, folder, _ = app
    release = controlled_model_job(monkeypatch, running, "failed")
    store = Store()
    original_tag, count = store.meta("vectors"), store.vector_count()
    source = (folder / "notes" / "rent.txt").read_bytes()
    assert request(running.server, "POST", "/api/models/change", {"model": modelfiles.MODELS[1].id, "confirmRebuild": True})[0] == 200
    assert running.model.id == modelfiles.DEFAULT and store.vector_count() == count
    assert request(running.server, "POST", "/api/models/change", {"model": modelfiles.DEFAULT, "confirmRebuild": True})[0] == 409
    assert request(running.server, "POST", "/api/settings", {"backend": "cpu"})[0] == 409
    if outcome == "cancelled":
        assert request(running.server, "POST", "/api/models/cancel", {})[0] == 200
    else:
        release.set()
    running._job.thread.join(5)
    assert not running._job.thread.is_alive()
    assert running._model_progress["state"] == outcome
    assert running.model.id == modelfiles.DEFAULT and store.meta("vectors") == original_tag and store.vector_count() == count
    assert (folder / "notes" / "rent.txt").read_bytes() == source
    assert json.loads(request(running.server, "POST", "/api/search", {"q": "banana"})[2])["hits"]
    store.close()


def test_model_switch_preserves_words_and_names_but_rebuilds_all_model_facts(app, monkeypatch):
    running, folder, _ = app
    release = controlled_model_job(monkeypatch, running)
    store = Store()
    files = store.known()
    store.set_meta("probes", "old probes")
    store.set_meta("explore_words", "old vocabulary")
    assert request(running.server, "POST", "/api/models/change", {"model": modelfiles.MODELS[1].id, "confirmRebuild": True})[0] == 200
    release.set()
    running._job.thread.join(5)
    assert running._model_progress["state"] == "done", running._model_progress
    assert running.model == modelfiles.MODELS[1] and store.vector_count() == 0
    assert store.known() == files and len(store.pending()) == len(files)
    assert store.meta("vectors") == running.model.vector_tag
    assert store.meta("search_model") == running.model.id
    assert store.meta("probes") is None and store.meta("explore_words") is None
    assert modelfiles.get() == running.model
    answer = json.loads(request(running.server, "POST", "/api/search", {"q": "banana"})[2])
    assert answer["hits"] and not answer["meaning"]
    assert (folder / "notes" / "rent.txt").read_bytes() == b"lease terms: the rent is 1,200 euros"
    store.close()


def test_legacy_model_identity_and_recovery_from_stale_settings(app):
    running, _, _ = app
    store = Store()
    original_count = store.vector_count()
    assert store.meta("search_model") is None and modelfiles.get().id == modelfiles.DEFAULT
    assert store.vector_count() == original_count
    with writer_lock(store):
        store.change_model(modelfiles.MODELS[1])
    assert running.settings.get("search_model", modelfiles.DEFAULT) == modelfiles.DEFAULT
    assert modelfiles.get().id == modelfiles.MODELS[1].id
    store.close()


def test_search_falls_back_to_words_if_the_query_model_does_not_match_the_index(app):
    running, _, _ = app
    engine = SearchEngine(None, model=modelfiles.MODELS[1])
    engine._embedder = type("Query", (), {"query": lambda self, q: word_vector(q), "close": lambda self: None})()
    try:
        answer = engine.search("banana")
        assert answer.hits and not answer.meaning
    finally:
        engine.stop()


def test_choosing_through_the_app_with_undo(app, tmp_path):
    running, folder, elsewhere = app
    rent = j(folder, "notes", "rent.txt")
    answer = json.loads(request(running.server, "POST", "/api/choose", {"op": "remove", "paths": [rent]})[2])
    assert answer["changed"] and rent in running.selection.removed
    before = answer["before"]
    request(running.server, "POST", "/api/choose", {"op": "restore", "selection": before})
    assert rent not in running.selection.removed
    key = touch(tmp_path / "more" / "server.pem", b"x")
    answer = json.loads(request(running.server, "POST", "/api/choose", {"op": "add", "paths": [key]})[2])
    assert answer["refused"] == {key: "key or password file"} and key not in running.selection.files
    saved = json.loads((Path(os.environ["DOWSE_HOME"]) / "settings.json").read_text())
    assert saved["files"] == [elsewhere]


def test_a_second_launch_hands_its_request_over(app, tmp_path):
    running, folder, _ = app
    new = touch(tmp_path / "Downloads" / "ticket.pdf", b"%PDF-1.4")
    assert launch.send({"op": "add", "paths": [new]})          # through app.json: port and token
    deadline = time.monotonic() + 5
    while new not in running.selection.files and time.monotonic() < deadline:
        time.sleep(0.05)
    assert new in running.selection.files                        # after the 400 ms gathering


def test_a_development_server_cannot_remove_the_running_apps_address(app):
    running, _, _ = app
    address = Path(os.environ["DOWSE_HOME"]) / "app.json"
    original = address.read_text()
    preview = App()
    preview.start(announce=False)
    try:
        preview.stop()
        assert address.read_text() == original
    finally:
        preview.server.stop()
    # Even another app in this process owns its own token; an older app must leave that address alone.
    replacement = json.loads(original) | {"token": "a-newer-apps-token"}
    address.write_text(json.dumps(replacement))
    running.stop()
    assert json.loads(address.read_text()) == replacement


def test_the_command_line_becomes_a_request(tmp_path):
    assert launch.message([]) == ({"op": "show"}, False)
    assert launch.message(["--background"]) == ({"op": "show"}, True)
    request_, background = launch.message(["--add", str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")])
    assert request_ == {"op": "add", "paths": [str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")]} and background
    assert launch.message(["--remove", "x.pdf"])[0] == {"op": "remove", "paths": [os.path.abspath("x.pdf")]}
    assert launch.message(["--library"]) == ({"op": "library"}, False)
    assert launch.message(["--search", str(tmp_path)]) == ({"op": "search", "paths": [str(tmp_path)]}, False)
    assert launch.message(["--restart"]) == ({"op": "restart"}, True)
    with pytest.raises(SystemExit):
        launch.message(["--add"])


def test_one_launch_holds_the_startup_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(launch.paths, "home", lambda: tmp_path)
    first, second = launch.StartupLock(), launch.StartupLock()
    assert first.acquire()
    assert not second.acquire()
    first.handle.close()
    assert second.acquire()


def test_what_the_notifications_say(tmp_path):
    folder = tmp_path / "Invoices"
    folder.mkdir()
    a, b = touch(tmp_path / "a.pdf"), touch(tmp_path / "b.pdf")
    key = touch(tmp_path / "server.pem")
    assert choice_message([a, b], {}, added=True, changed=True)[0] == "Added 2 files to Dowse"
    assert choice_message([str(folder), a], {}, added=True, changed=True)[0] == "Added “Invoices” and 1 file to Dowse"
    title, text = choice_message([a, key], {key: "key or password file"}, added=True, changed=True)
    assert title == "Added 1 file to Dowse" and "Can't add server.pem: key or password file." in text
    assert choice_message([key], {key: "key or password file"}, added=True, changed=False)[0] == "Nothing added"
    assert choice_message([a], {}, added=False, changed=True)[0] == "Removed 1 file from Dowse"


# Explore's map

def test_explore_groups_files_by_what_is_in_them(tmp_path):
    root = tmp_path / "files"
    for i in range(12):   # two kinds of thing: a shared word pulls each group's vectors together
        touch(root / f"beach {i}.txt", f"beach sand sea {i}".encode())
        touch(root / f"invoice {i}.txt", f"invoice total due {i}".encode())
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, workers=2)
    indexer.sync(Selection((str(root),)))
    indexer.run(FakeEmbedder())
    built = explore.build(store, FakeEmbedder())
    assert built["total"] == 24 and len(built["pieces"]) == 24
    by_aisle: dict[int, set[str]] = {}
    for piece in built["pieces"]:
        by_aisle.setdefault(piece["aisle"], set()).add(Path(piece["path"]).name.split()[0])
    assert all(len(kinds) == 1 for kinds in by_aisle.values())   # no aisle mixes beaches and invoices
    assert len(by_aisle) >= 2
    assert explore.word_vectors(store, None) is not None          # embedded once, kept with the index
    labels, centres = explore.kmeans(np.eye(4, dtype=np.float32).repeat(3, axis=0), 4)
    assert len(set(labels.tolist())) == 4 and centres.shape == (4, 4)


# The query model sleeps when search isn't used

def test_the_query_model_sleeps_and_wakes(monkeypatch, tmp_path):
    from dowse.desktop import engine as engine_module

    started, stopped, states = [], [], []

    class FakeServer:
        def __init__(self, choice, text_only=False, **kwargs):
            self.url = "http://stand-in"

        def start(self, **kwargs):
            started.append(1)
            return self

        def stop(self):
            stopped.append(1)

    class FakeQueryEmbedder:
        def __init__(self, url, parallel=1, **kwargs):
            pass

        def query(self, text):
            return word_vector(text)

        def close(self):
            pass

    Store(tmp_path / "index.sqlite").close()
    monkeypatch.setattr(engine_module.runtime, "Server", FakeServer)
    monkeypatch.setattr(engine_module, "Embedder", FakeQueryEmbedder)
    monkeypatch.setattr(engine_module.probes, "ensure", lambda store, embedder: None)
    monkeypatch.setattr(engine_module, "default_file", lambda: tmp_path / "index.sqlite")
    engine = SearchEngine(runtime.Choice("cpu", None), idle=0.3, on_state=states.append)
    assert engine.state == "asleep" and not started
    assert not engine.search("").meaning and not started        # an empty search doesn't wake it
    engine.warm()
    deadline = time.monotonic() + 5
    while engine.state != "ready" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert engine.search("lease").meaning and started == [1]
    while engine.state != "asleep" and time.monotonic() < deadline + 5:
        time.sleep(0.05)
    assert stopped == [1] and states[-1] == "asleep"
    engine.stop()


# The windows' process

def test_the_windows_process_protocol(tmp_path):
    """Commands go to the windows' process on its stdin; answers come back by id on its stdout."""
    stand_in = tmp_path / "windows.py"
    stand_in.write_text(
        "import json, sys\n"
        "print(json.dumps({'event': 'ready'}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    c = json.loads(line)\n"
        "    if c.get('op') == 'quit': break\n"
        "    print(json.dumps({'id': c.get('id'), 'result': [c.get('op'), c.get('what')]}), flush=True)\n")
    host = WindowHost("http://127.0.0.1:1/", "token", dark=False, command=[sys.executable, str(stand_in)])
    assert host.call("pick", wait=5, what="folder") == ["pick", "folder"]
    assert host.running()
    host.stop()
    assert not host.running()
    assert host.call("pick", wait=5, what="files") == ["pick", "files"]   # started again on demand
    host.stop()


# How long reading takes, and when indexing waits

def test_speed_is_remembered_per_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(speed.paths, "home", lambda: tmp_path)
    gpu, cpu = runtime.Choice("vulkan", "Vulkan1"), runtime.Choice("cpu", None)
    assert speed.load(gpu, True) == speed.GUESS[True] and not speed.load(gpu, True).measured
    measured = speed.Speed(photo=0.2, screenshot=0.3, page=0.3, frame=0.1, passage=0.05, sound=0.4, ocr=0.05,
                           device=gpu.describe())
    speed.save(measured)
    assert speed.load(gpu, True) == measured and speed.load(cpu, False) == speed.GUESS[False]
    options = Options.for_device(True)
    assert speed.file_seconds(measured, Kind.VIDEO, options) > speed.file_seconds(measured, Kind.AUDIO, options) > \
        speed.file_seconds(measured, Kind.IMAGE, options)
    on = {g: speed.seconds({Kind.IMAGE: 1000, Kind.VIDEO: 10}, speed.GUESS[g], Options.for_device(g)) for g in (True, False)}
    assert on[True] < on[False]


def test_power_pauses_indexing(monkeypatch):
    indexing = Indexing(Config(None, Selection()))
    monkeypatch.setattr(winapi, "power", lambda: (True, False))
    assert indexing.power_reason() is None                     # on battery, but allowed
    indexing.config = Config(None, Selection(), plugged_in_only=True)
    assert indexing.power_reason() == "waiting to be plugged in"
    monkeypatch.setattr(winapi, "power", lambda: (False, True))
    assert indexing.power_reason() == "Battery Saver is on"    # always, whatever the setting
    indexing.close()


def test_clearing_and_counting_the_index(tmp_path):
    store = Store(tmp_path / "index.sqlite")
    folder = str(tmp_path / "files")
    with store.transaction():
        for name in ("a.jpg", "b.jpg", os.path.join("sub", "c.pdf")):
            store.add_file(os.path.join(folder, name), Kind.IMAGE, 1, 1.0)
        store.add_file(str(tmp_path / "filesystem.jpg"), Kind.IMAGE, 1, 1.0)   # a sibling sharing the prefix
        store.set_meta("probes", "kept")
    assert store.count_at(folder) == 3
    store.clear()
    assert store.count_at(folder) == 0 and not store.files() and store.meta("probes") == "kept"


# Windows itself

def test_hotkeys_parse():
    assert winapi.parse_hotkey("Alt+Shift+Space") == (winapi.MOD_ALT | winapi.MOD_SHIFT, 0x20)
    assert winapi.parse_hotkey("ctrl + F12") == (winapi.MOD_CONTROL, 0x7B)
    for bad in ("Space", "Alt+Banana", "", "Hyper+K"):
        with pytest.raises(ValueError):
            winapi.parse_hotkey(bad)


@windows   # SumatraPDF, Windows' "open at page"
def test_opening_at_the_place():
    sumatra = r"C:\Program Files\SumatraPDF\SumatraPDF.exe"
    assert system.open_command(r"C:\docs\lease.pdf", Kind.PDF, 7.0, sumatra) == \
        [sumatra, "-reuse-instance", "-page", "7", r"C:\docs\lease.pdf"]
    edge = system.open_command(r"C:\docs\lease.pdf", Kind.PDF, 7.0,
                               r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    assert edge[1].startswith("file:///") and edge[1].endswith("#page=7")
    assert system.open_command(r"C:\v\clip.mkv", Kind.VIDEO, 95.5, r"C:\VLC\vlc.exe") == \
        [r"C:\VLC\vlc.exe", "--start-time=95", r"C:\v\clip.mkv"]
    assert system.open_command(r"C:\v\clip.mkv", Kind.VIDEO, 95.5, r"C:\Apps\Unknown.exe") is None
    assert system.open_command(r"C:\a.jpg", Kind.IMAGE, None, r"C:\x.exe") is None


@windows
def test_the_clipboard_takes_a_path_and_a_file(tmp_path):
    import ctypes

    target = touch(tmp_path / "report.pdf", b"%PDF")
    system.copy_path(target)
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    while not user32.OpenClipboard(None):
        time.sleep(0.02)
    try:
        assert ctypes.wstring_at(kernel32.GlobalLock(user32.GetClipboardData(13))) == target
    finally:
        user32.CloseClipboard()
    system.copy_file(target)
    while not user32.OpenClipboard(None):
        time.sleep(0.02)
    try:
        assert user32.IsClipboardFormatAvailable(15)   # CF_HDROP: Explorer can paste it
    finally:
        user32.CloseClipboard()


@windows
def test_explorer_registration(tmp_path):
    import winreg

    from dowse.desktop import shell

    classes = rf"Software\DowseTest{os.getpid()}\Classes"
    try:
        assert not shell.registered(classes)
        shell.register(classes, send_to=tmp_path)
        assert shell.registered(classes)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{classes}\*\shell\Dowse\shell\1add\command") as key:
            command = winreg.QueryValueEx(key, "")[0]
        assert command.endswith('--add "%1"') and "dowse.desktop.launch" in command
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf"{classes}\Directory\Background\shell\DowseSearch\command") as key:
            assert winreg.QueryValueEx(key, "")[0].endswith('--search "%V"')
        assert (tmp_path / "Dowse.lnk").exists()
        shell.unregister(classes, send_to=tmp_path)
        assert not shell.registered(classes) and not (tmp_path / "Dowse.lnk").exists()
    finally:
        shell._delete_tree(rf"Software\DowseTest{os.getpid()}")


@windows
def test_many_launches_at_once_all_arrive(app, tmp_path):
    """Explorer starts one launch per selected file: twelve at once all reach the app, as one change."""
    running, _, _ = app
    files = [touch(tmp_path / "burst" / f"scan {i}.pdf", b"%PDF") for i in range(12)]
    threads = [threading.Thread(target=launch.send, args=({"op": "add", "paths": [f]},)) for f in files]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    deadline = time.monotonic() + 5
    while not all(f in running.selection.files for f in files) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert all(f in running.selection.files for f in files)


def test_aisles_through_the_app(app):
    running, folder, elsewhere = app
    rent = j(folder, "notes", "rent.txt")
    draft = {"name": "Housing", "about": "leases and rent", "pinned": [rent], "excluded": []}
    preview = json.loads(request(running.server, "POST", "/api/aisles/preview", draft)[2])
    assert preview["count"] == 1 and preview["members"][0]["pinned"] and preview["members"][0]["name"] == "rent.txt"
    assert request(running.server, "POST", "/api/aisles/preview", {"name": " "})[0] == 400
    saved = json.loads(request(running.server, "POST", "/api/aisles/save", draft)[2])
    listing = json.loads(request(running.server, "GET", "/api/aisles")[2])
    assert [(a["name"], a["count"]) for a in listing["aisles"]] == [("Housing", 1)]
    assert all(s["count"] and s["label"] for s in listing["suggestions"])   # the rest, grouped: offered
    members = json.loads(request(running.server, "GET", f"/api/aisles/members?id={saved['id']}")[2])
    assert [m["name"] for m in members["members"]] == ["rent.txt"]
    explored = json.loads(request(running.server, "GET", "/api/explore")[2])
    assert explored["aisles"][0]["label"] == "Housing" and "membership" not in explored
    label = listing["suggestions"][0]["label"]
    request(running.server, "POST", "/api/aisles/dismiss", {"label": label})
    assert label not in [s["label"] for s in json.loads(request(running.server, "GET", "/api/aisles")[2])["suggestions"]]
    request(running.server, "POST", "/api/aisles/auto", {"on": False})   # the rest all in "Everything else"
    explored = json.loads(request(running.server, "GET", "/api/explore")[2])
    assert [a["label"] for a in explored["aisles"]] == ["Housing", "Everything else"]
    request(running.server, "POST", "/api/aisles/delete", {"id": saved["id"]})
    assert json.loads(request(running.server, "GET", "/api/aisles")[2])["aisles"] == []


def test_paging_through_a_documents_passages(app):
    running, folder, elsewhere = app
    rent = j(folder, "notes", "rent.txt")
    first = json.loads(request(running.server, "GET", f"/api/passage?segment=0&q=rent&path={rent}&loc=0")[2])
    assert first["loc"] == 0 and first["count"] == 1 and "«rent»" in first["text"]
    beyond = json.loads(request(running.server, "GET", f"/api/passage?segment=0&q=&path={rent}&loc=1")[2])
    assert beyond["text"] is None and beyond["count"] == 1   # past the last passage: nothing, and how many there are
