"""Multiple search folders form a union, with legacy callers and ranking kept intact."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

import pytest

from dowse.searcher import Searcher
from dowse.store import Store
from test_desktop import app, request


def search(running, within, **extra):
    status, _, raw = request(running.server, "POST", "/api/search", {"q": "banana", "within": within, **extra})
    assert status == 200, raw
    return json.loads(raw)


def test_multiple_folders_search_any_selected_folder_not_their_intersection(app):
    running, root, named = app
    selected = [str(root / "notes"), str(Path(named).parent)]
    answer = search(running, selected)
    assert {Path(h["path"]).name for h in answer["hits"]} == {"cake.txt", "recipe.txt"}
    assert [f["label"] for f in answer["filters"] if f["id"] == "folder"] == selected
    assert len(search(running, [selected[0]])["hits"]) == 1
    assert len(search(running, [selected[1]])["hits"]) == 1


def test_single_folder_compatibility_and_no_duplicate_hits_for_overlapping_folders(app):
    running, root, _ = app
    legacy = search(running, str(root))
    one = search(running, [str(root)])
    # the same folder written another way: on Windows other letter case and a trailing backslash; on a Mac (whose disk
    # ignores case) other letter case and a trailing slash; elsewhere a trailing slash
    same = {"win32": str(root).upper() + "\\", "darwin": str(root).upper() + os.sep}.get(sys.platform, str(root) + os.sep)
    overlap = search(running, [str(root), str(root / "notes"), same])
    assert one["hits"] == legacy["hits"] == overlap["hits"]
    assert len(overlap["filters"]) == 2  # equivalent spellings deduplicated; parent/child retained
    assert len(search(running, [])['hits']) == 2  # empty means all folders


def test_missing_scope_does_not_hide_another_selected_folder(app):
    running, root, _ = app
    assert len(search(running, [str(root / "missing"), str(root / "notes")])["hits"]) == 1
    assert search(running, [str(root / "missing")])["hits"] == []


def test_multi_folder_recent_browsing_uses_the_same_union_and_deduplicates(app):
    running, root, named = app
    selected = [str(root / "notes"), str(Path(named).parent), str(root / "notes")]
    route = "/api/recent?" + urlencode({"folders": json.dumps(selected)})
    status, _, raw = request(running.server, "GET", route)
    hits = json.loads(raw)["hits"]
    assert status == 200 and {h["name"] for h in hits} == {"cake.txt", "rent.txt", "recipe.txt"}
    assert len({h["path"] for h in hits}) == len(hits) == 3


def test_multiple_scopes_combine_with_date_and_type_without_changing_membership(app):
    running, root, named = app
    before = running.selection
    answer = search(running, [str(root / "notes"), str(Path(named).parent)], kinds=["doc"],
                    date={"from": "1970-01-01", "to": "2099-12-30"})
    assert len(answer["hits"]) == 2
    assert [f["id"] for f in answer["filters"]] == ["folder", "folder", "kind", "date"]
    assert running.selection == before


def test_multiple_scopes_obey_immediate_removal_and_suspension(app):
    running, root, named = app
    selected = [str(root / "notes"), str(Path(named).parent)]
    running.selection = running.selection.deactivate([selected[0]])
    assert [h["path"] for h in search(running, selected)["hits"]] == [named]
    running.selection = running.selection.remove([named])
    assert search(running, selected)["hits"] == []


def test_folder_union_keeps_global_scores_order_and_index_data_unchanged(app):
    running, root, named = app
    store = Store()
    before = list(store.db.execute("SELECT id, path, active FROM files ORDER BY id")), store.vector_count()
    searcher = Searcher(store)
    expected = searcher.search("banana", None)
    union = searcher.search("banana", None, within=[str(root / "notes"), str(Path(named).parent)])
    assert [(h.path, h.score, h.loc) for h in union] == [(h.path, h.score, h.loc) for h in expected]
    assert (list(store.db.execute("SELECT id, path, active FROM files ORDER BY id")), store.vector_count()) == before
    store.close()


@pytest.mark.parametrize("within", [True, 4, {}, [None], [""], ["ok", 42]])
def test_invalid_multi_folder_controls_are_bad_requests(app, within):
    running, _, _ = app
    assert request(running.server, "POST", "/api/search", {"q": "banana", "within": within})[0] == 400
    route = "/api/recent?" + urlencode({"folders": json.dumps(within)})
    assert request(running.server, "GET", route)[0] == 400
