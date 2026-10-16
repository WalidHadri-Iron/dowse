"""Every searchable file is reachable, with bounded pages, consistent filters, and no model wakeup."""

import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

from dowse.rules import Kind
from dowse.store import Store, NewSegment, Segment, Modality
from test_desktop import app, request


def browse(running, **params):
    status, _, raw = request(running.server, "GET", "/api/browse?" + urlencode(params))
    assert status == 200, raw
    return json.loads(raw)


def seed(root, count=210, mtime=1_720_000_000):
    store = Store()
    for i in range(count):
        file = store.add_file(str(root / f"record-{i:03}.txt"), Kind.DOC, 20, mtime)
        store.add_segments(file, [NewSegment(Segment.CHUNK, Modality.TEXT, loc=0, body=f"archive record {i}")])
        store.finish(file, "done")
    store.close()


def test_browse_reaches_every_file_with_stable_tied_dates_and_no_duplicates(app):
    running, root, _ = app
    seed(root)
    pages = [browse(running, offset=offset) for offset in (0, 80, 160)]
    assert [len(p["hits"]) for p in pages] == [80, 80, 54]
    assert [p["offset"] for p in pages] == [0, 80, 160]
    assert all(p["total"] == 214 and p["limit"] == 80 for p in pages)
    assert [p["hasMore"] for p in pages] == [True, True, False]
    paths = [h["path"] for p in pages for h in p["hits"]]
    store = Store()
    expected = [path for (path,) in store.db.execute("SELECT path FROM files ORDER BY mtime DESC, id DESC")]
    store.close()
    assert paths == expected and len(set(paths)) == 214
    assert all(h["excerpt"] for p in pages for h in p["hits"] if h["kind"] == "doc")


def test_browse_count_and_pages_respect_multi_folders_kind_and_date(app):
    running, root, named = app
    seed(root)
    page = browse(running, folders=json.dumps([str(root / "notes"), str(Path(named).parent)]), kinds="doc",
                  **{"from": "1970-01-01", "to": "2099-12-30"})
    assert page["total"] == 3 and not page["hasMore"]
    assert {h["name"] for h in page["hits"]} == {"cake.txt", "rent.txt", "recipe.txt"}
    assert browse(running, kinds="image")["total"] == 1
    assert browse(running, to="1970-01-01")["total"] == 0


def test_browse_has_no_hidden_scan_limit_before_selection_gates(app):
    running, root, _ = app
    seed(root.parent / "stale-unselected", 300, mtime=2_000_000_000)
    page = browse(running)
    assert page["total"] == 4 and len(page["hits"]) == 4
    assert all("stale-unselected" not in h["path"] for h in page["hits"])


def test_browse_clamps_old_pages_and_immediately_hides_inactive_removed_files(app):
    running, root, named = app
    seed(root)
    running.selection = running.selection.deactivate([str(root)])
    page = browse(running, offset=160)
    assert page["offset"] == 0 and page["total"] == 1 and [h["path"] for h in page["hits"]] == [named]
    running.selection = running.selection.remove([named])
    page = browse(running, offset=160)
    assert page["offset"] == 0 and page["total"] == 0 and page["hits"] == [] and not page["hasMore"]


def test_browse_never_wakes_model_or_changes_sources_or_index(app, monkeypatch):
    running, root, _ = app
    before = running.selection
    def forbidden():
        raise AssertionError("Browsing does not need the query model")
    monkeypatch.setattr(running.engine, "warm", forbidden)
    store = Store()
    rows, vectors = list(store.db.execute("SELECT id, path, active FROM files ORDER BY id")), store.vector_count()
    assert browse(running, limit=1)["total"] == 4
    assert running.selection == before
    assert list(store.db.execute("SELECT id, path, active FROM files ORDER BY id")) == rows
    assert store.vector_count() == vectors
    store.close()


@pytest.mark.parametrize("params", [{"offset": -1}, {"offset": "bad"}, {"limit": "bad"}, {"folders": "bad json"}])
def test_invalid_browse_pages_are_bad_requests_not_crashes(app, params):
    running, _, _ = app
    assert request(running.server, "GET", "/api/browse?" + urlencode(params))[0] == 400


def test_page_size_is_bounded_but_never_limits_the_collection(app):
    running, root, _ = app
    seed(root)
    page = browse(running, limit=1000)
    assert page["limit"] == 500 and len(page["hits"]) == page["total"] == 214 and not page["hasMore"]
    last = browse(running, offset=9999)
    assert last["offset"] == 160 and len(last["hits"]) == 54 and not last["hasMore"]
