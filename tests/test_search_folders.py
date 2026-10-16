"""The search scope chooser lists Dowse's sources, never scans arbitrary disk folders."""

import json
from pathlib import Path

from dowse import catalog
from dowse.selection import Selection
from dowse.store import Store
from test_desktop import app, request


def folders(running):
    status, _, body = request(running.server, "GET", "/api/search/folders")
    assert status == 200, body
    return {f["path"]: f for f in json.loads(body)["folders"]}


def test_folder_choices_include_added_roots_indexed_subfolders_and_named_file_parents(app):
    running, root, named = app
    before = running.selection
    choices = folders(running)
    assert set(choices) == {str(root), str(root / "notes"), str(Path(named).parent)}
    assert choices[str(root)]["count"] == 3
    assert choices[str(root / "notes")]["count"] == 2
    assert choices[str(Path(named).parent)]["count"] == 1
    assert choices[str(root)]["isRoot"] and not choices[str(root / "notes")]["isRoot"]
    assert all(f["active"] for f in choices.values())
    assert running.selection == before  # choosing/filtering never changes membership


def test_folder_choices_apply_suspensions_immediately_even_before_the_index_changes(app):
    running, root, named = app
    running.selection = running.selection.deactivate([str(root / "notes")])
    choices = folders(running)
    assert choices[str(root)]["count"] == 1
    assert choices[str(root / "notes")]["count"] == 0 and not choices[str(root / "notes")]["active"]
    running.selection = running.selection.deactivate([str(root)])
    choices = folders(running)
    assert choices[str(root)]["count"] == 0 and not choices[str(root)]["active"]
    assert choices[str(Path(named).parent)]["count"] == 1


def test_removed_folders_and_stale_unselected_files_are_not_offered(app):
    running, root, _ = app
    running.selection = running.selection.remove([str(root / "notes")])
    choices = folders(running)
    assert choices[str(root)]["count"] == 1 and str(root / "notes") not in choices
    running.selection = running.selection.remove([str(root)])
    choices = folders(running)
    assert str(root) not in choices
    assert str(root / "notes") not in choices
    running.selection = Selection()
    assert folders(running) == {}


def test_chooser_does_not_scan_the_filesystem_and_keeps_empty_added_roots(tmp_path, monkeypatch):
    store = Store(tmp_path / "index.sqlite")
    root = str(tmp_path / "unplugged-source")  # deliberately not present on disk
    chosen = Selection((root,))
    def forbidden(*args, **kwargs):
        raise AssertionError("Folder filtering must not scan or stat disk contents")
    monkeypatch.setattr(catalog.os, "scandir", forbidden)
    monkeypatch.setattr(catalog.os.path, "isdir", forbidden)
    assert catalog.search_folders(store, chosen) == [
        {"path": root, "name": "unplugged-source", "root": root, "isRoot": True, "count": 0, "active": True}]
    store.close()


def test_nested_roots_count_each_file_once(app):
    running, root, _ = app
    running.selection = Selection((str(root), str(root / "notes")), running.selection.files)
    choices = folders(running)
    assert choices[str(root)]["count"] == 3
    assert choices[str(root / "notes")]["count"] == 2
    assert choices[str(root / "notes")]["isRoot"]
