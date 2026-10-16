"""Temporary search suspension, permanent collection removal, and safely returning to search."""

from pathlib import Path

import pytest

from dowse import catalog, crawler, explore, modelfiles
from dowse.indexer import Indexer
from dowse.searcher import Searcher
from dowse.selection import Selection, Source
from dowse.service import IndexService
from dowse.store import Store
from dowse.desktop.indexing import Config, Indexing
from dowse import runtime
from test_index import FakeEmbedder, word_vector
from test_selection import touch


@pytest.fixture
def collection(tmp_path):
    root = tmp_path / "files"
    apple = touch(root / "apple.txt", b"apple recipe for breakfast")
    banana = touch(root / "notes" / "banana.txt", b"banana bread recipe")
    outside = touch(tmp_path / "outside" / "banana.txt", b"banana cake recipe")
    store = Store(tmp_path / "index.sqlite")
    chosen = Selection((str(root),), (outside, banana))
    indexer = Indexer(store)
    indexer.sync(chosen)
    indexer.run(FakeEmbedder())
    yield store, indexer, chosen, root, apple, banana, outside
    store.close()


def test_deactivation_keeps_sources_and_covers_named_files(collection):
    _, _, chosen, root, apple, banana, outside = collection
    after = chosen.deactivate([banana]).deactivate([str(root)])
    assert after.folders == chosen.folders and after.files == chosen.files
    assert after.decide(apple) is None and after.decide(banana) is None
    assert after.configured(banana) is Source.NAMED
    assert after.decide(outside) is Source.NAMED
    assert after.inactive_by(banana) == str(root)
    assert after.activate([str(root)]).inactive == (banana,)
    assert after.activate([banana]).decide(banana) is None  # the parent is still off
    assert Selection.from_settings(after.to_settings({})) == after
    assert crawler.crawl(after).files[0].path == outside


def test_suspension_preserves_vectors_and_fts_and_activation_reuses_them(collection):
    store, indexer, chosen, root, apple, banana, outside = collection
    before = list(store.db.execute("SELECT * FROM segments ORDER BY id"))
    after = chosen.deactivate([str(root)])
    indexer.update([str(root)], after)
    indexer.sync(after)  # regular resync must not discard paused files
    assert list(store.db.execute("SELECT * FROM segments ORDER BY id")) == before
    assert not {f.path for f in store.pending()} & {apple, banana}
    assert {e.path for e in catalog.entries(store, after, catalog.ALL) if not e.active} == {apple, banana}
    assert {e.path for e in catalog.entries(store, after, catalog.INACTIVE)} == {str(root)}
    assert {h.path for h in Searcher(store).search("banana", word_vector("banana"))} == {outside}
    assert all(p["path"] == outside for p in explore.build(store)["pieces"])
    indexer.update([str(root)], after.activate([str(root)]))
    assert list(store.db.execute("SELECT * FROM segments ORDER BY id")) == before
    assert {h.path for h in Searcher(store).search("banana", word_vector("banana"))} == {banana, outside}


def test_inactive_content_is_never_read_or_requeued_until_reactivation(collection, monkeypatch):
    store, indexer, chosen, root, apple, banana, _ = collection
    after = chosen.deactivate([str(root)])
    indexer.sync(after)
    touch(Path(apple), b"changed apple recipe with cinnamon")
    new = touch(root / "new.txt", b"new recipe")
    before = store.vector_count()
    service = IndexService(store, after, indexer.options, lambda: (FakeEmbedder(), lambda: None))
    service._requeue([str(root)])
    indexer.update([apple, new], after)
    assert store.vector_count() == before and new not in store.known()
    resumed = after.activate([str(root)])
    summary = indexer.sync(resumed)
    assert summary.changed == 1 and summary.added == 1
    assert {f.path for f in store.pending()} == {apple, new}


def test_removal_discards_cached_data_but_remembers_sources_for_add_back(collection):
    store, indexer, chosen, root, apple, banana, outside = collection
    after = chosen.deactivate([str(root)]).remove([str(root), outside])
    assert not after.folders and not after.files and not after.inactive
    assert set(after.forgotten) == {str(root), outside}
    assert {e.path for e in catalog.entries(store, after, catalog.REMOVED)} == {str(root), outside}
    indexer.sync(after)
    assert not store.known() and store.vector_count() == 0
    assert Path(apple).read_bytes() == b"apple recipe for breakfast"
    returned, refused = catalog.add(after, [str(root), outside])
    assert not refused and not returned.forgotten and returned.decide(banana) is Source.FOLDER


def test_nested_roots_leave_together_when_their_parent_is_removed(tmp_path):
    root = str(tmp_path / "root")
    chosen = Selection((root, str(tmp_path / "root" / "child")))
    after = chosen.remove([root])
    assert not after.folders and after.forgotten == (root,)


def test_removing_the_last_source_still_reconciles_the_running_service(collection):
    store, indexer, chosen, *_ = collection
    service = IndexService(store, chosen, indexer.options, lambda: (FakeEmbedder(), lambda: None))
    class Watcher:
        def watch(self, *args):
            pass
    service._choose(Selection(), Watcher())
    assert not store.known()
    choice = runtime.Choice("cpu", None)
    manager = Indexing(Config(choice, chosen), on_status=lambda _: None, on_changed=lambda: None)
    try:
        manager._service = service
        manager.started = True
        manager.configure(Config(choice, Selection()))
        assert service._next == Selection() and manager._service is service
    finally:
        manager.close()


def test_last_source_removal_finishes_after_restart_even_without_a_model(collection, monkeypatch):
    store, _, chosen, root, apple, *_ = collection
    monkeypatch.setattr("dowse.desktop.indexing.default_file", lambda: store.path)
    manager = Indexing(Config(None, chosen.remove([str(root), *chosen.files])))
    try:
        manager.start()
        manager._thread.join(5)
        assert not manager._thread.is_alive() and not store.known()
        assert Path(apple).read_bytes() == b"apple recipe for breakfast"
    finally:
        manager.close()


def test_legacy_schema_is_migrated_without_losing_segments(collection, tmp_path, monkeypatch):
    store, _, _, _, _, _, _ = collection
    before = list(store.db.execute("SELECT * FROM segments ORDER BY id"))
    # Recreate a version-one fixture by dropping only the new column.
    store.db.execute("ALTER TABLE files DROP COLUMN active")
    store.db.execute("PRAGMA user_version = 1")
    monkeypatch.setenv("DOWSE_HOME", str(tmp_path))
    monkeypatch.setattr("dowse.store.default_file", lambda: store.path)
    assert modelfiles.active_id() == modelfiles.DEFAULT
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 1  # model lookup must not migrate
    reader = Store(store.path)
    try:
        assert reader.db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert all(r[0] == 1 for r in reader.db.execute("SELECT active FROM files"))
        assert list(reader.db.execute("SELECT * FROM segments ORDER BY id")) == before
    finally:
        reader.close()
