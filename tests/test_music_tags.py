"""Metadata upgrades, bounded parsing and mixed-library ranking, without touching a real collection/model."""
import io

import pytest

from dowse.extract import media, music_tags
from dowse.indexer import Indexer
from dowse.reading import Options, _tag_pieces
from dowse.rules import Kind
from dowse.searcher import Searcher
from dowse.selection import Selection
from dowse.store import Modality, NewSegment, Segment, Store
from music_fixtures import flac, id3
from test_index import FakeEmbedder
from test_media import write_audio


@pytest.mark.parametrize("extension,fixture,artist", [("mp3", id3, "The Skylarks"),
                                                     ("flac", flac, "The Night Owls; Second Artist")])
def test_large_cover_art_is_skipped_not_a_reason_to_discard_tags(tmp_path, monkeypatch, extension, fixture, artist):
    path = fixture(tmp_path / f"song.{extension}", artwork=3 << 20)
    reads = []
    original = music_tags._Bounded.read
    def measured(self, size=-1):
        data = original(self, size)
        reads.append(len(data))
        return data
    monkeypatch.setattr(music_tags._Bounded, "read", measured)
    assert music_tags.read(str(path))["artist"] == artist
    assert sum(reads) < 4096   # not a scaled-down copy of the multi-megabyte picture


def test_a_large_text_frame_cannot_allocate_unbounded_metadata(tmp_path):
    path = id3(tmp_path / "song.mp3", fields={"TIT2": "x" * (2 << 20)})
    assert music_tags.read(str(path)) == {}


def test_operation_budget_bounds_pathological_metadata():
    reader = music_tags._Bounded(io.BytesIO(b"x"))
    for _ in range(music_tags.MAX_OPERATIONS):
        reader.seek(0)
    with pytest.raises(ValueError, match="operation budget"):
        reader.seek(0)


def test_cloud_placeholders_are_never_opened(monkeypatch):
    monkeypatch.setattr(music_tags, "attributes", lambda path: 0)
    monkeypatch.setattr(music_tags, "cloud_only", lambda flags: True)
    def forbidden(*args):
        raise AssertionError("downloaded an online-only file")
    monkeypatch.setattr(music_tags, "open", forbidden, raising=False)
    assert music_tags.read("online.mp3") == {}


def test_unicode_multivalue_and_bounded_values(tmp_path):
    path = id3(tmp_path / "song.mp3", version=4,
               fields={"TIT2": "  Café\n" + "été " * 100, "TPE1": "ليلى\0坂本"})
    values = music_tags.read(str(path))
    assert values["title"].startswith("Café été") and len(values["title"]) == music_tags.MAX_VALUE_CHARS
    assert values["artist"] == "ليلى; 坂本"


@pytest.mark.parametrize("extension,codec", [("m4a", "aac"), ("ogg", "libopus"), ("wav", "pcm_s16le")])
def test_other_audio_containers_share_the_same_metadata_reader(tmp_path, extension, codec):
    path = tmp_path / f"song.{extension}"
    write_audio(path, 2, rate=48000, codec=codec, metadata={"artist": "The Skylarks", "title": "Midnight Lantern"})
    tags = music_tags.read(str(path))
    assert tags["artist"] == "The Skylarks" and tags["title"] == "Midnight Lantern"


def test_metadata_field_labels_do_not_become_fake_matching_words(tmp_path):
    pieces = _tag_pieces({"title": "Midnight Lantern", "artist": "The Skylarks", "album": "Winter Stories"})
    store = Store(tmp_path / "index.sqlite")
    file = store.add_file(str(tmp_path / "song.mp3"), Kind.AUDIO, 100, 1)
    store.add_segments(file, [NewSegment(Segment.TAG, Modality.NONE, body=pieces[0].body)])
    store.finish(file, "done")
    assert Searcher(store).search("artist album title", None) == []
    store.close()


def test_tags_remain_searchable_when_the_sound_is_unreadable(tmp_path):
    path = id3(tmp_path / "song.mp3")  # valid tags, no audio stream
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store)
    indexer.sync(Selection(files=(str(path),)))
    embedder = FakeEmbedder()
    indexer.run(embedder)
    assert embedder.embedded == 0
    assert store.file(1).state == "done"
    hit, = Searcher(store).search("Skylarks", None)
    assert hit.loc is None and hit.segment == "tag"
    store.close()


@pytest.mark.parametrize("tagged", [False, True])
@pytest.mark.parametrize("state", ["done", "partial", "skipped"])
def test_metadata_upgrade_preserves_vectors_and_resume_points(tmp_path, monkeypatch, tagged, state):
    path = tmp_path / "song.mp3"
    write_audio(path, 6, codec="libmp3lame", silent_from=0 if state == "skipped" else None)
    if tagged:
        id3(path)
    original_file = path.read_bytes()
    store = Store(tmp_path / "index.sqlite")
    options = Options(audio_window=2, audio_hop=2, media_step=2)
    indexer = Indexer(store, options)
    selection = Selection(files=(str(path),))
    indexer.sync(selection)
    indexer.run(FakeEmbedder())
    saved = store.file(1)
    old_info = saved.info | {"reader": 1}
    old_info.pop("music_tags", None)
    store.db.execute("DELETE FROM segments WHERE kind = 'tag'")
    if state == "partial":
        old_info["read_to"] = 2
        store.db.execute("DELETE FROM segments WHERE kind = 'audio' AND loc >= 2")
    store.finish(saved.id, state, info=old_info)
    before = list(store.db.execute("SELECT id, vec, loc, loc_end FROM segments WHERE vec IS NOT NULL ORDER BY id"))
    indexer.sync(selection)
    embedder = FakeEmbedder()
    def forbidden(*args):
        raise AssertionError("metadata upgrade decoded the sound")
    with monkeypatch.context() as guard:
        guard.setattr(media, "open_media", forbidden)
        guard.setattr(media, "sound", forbidden)
        assert indexer.run(embedder, should_stop=lambda: True) == 1
    after = list(store.db.execute("SELECT id, vec, loc, loc_end FROM segments WHERE vec IS NOT NULL ORDER BY id"))
    assert before == after and embedder.embedded == 0
    saved = store.file(1)
    assert saved.info["reader"] == 3
    assert store.db.execute("SELECT COUNT(*) FROM segments WHERE kind='tag'").fetchone()[0] == int(tagged)
    if state == "partial":
        assert saved.state == "partial" and saved.info["read_to"] == 2
        indexer.run(embedder)
        for row in before:
            assert row in list(store.db.execute("SELECT id, vec, loc, loc_end FROM segments WHERE vec IS NOT NULL"))
        assert store.file(1).state == "done"
    indexer.sync(selection)
    assert indexer.run(FakeEmbedder()) == 0
    assert path.read_bytes() == original_file
    store.close()


def test_metadata_only_model_upgrades_without_decoding_sound(tmp_path, monkeypatch):
    path = id3(tmp_path / "song.mp3")
    store = Store(tmp_path / "index.sqlite")
    file = store.add_file(str(path), Kind.AUDIO, path.stat().st_size, path.stat().st_mtime)
    store.finish(file, "done", info={"reader": 1})
    indexer = Indexer(store, Options(embed_media=False))
    monkeypatch.setattr(media, "open_media", lambda *a: pytest.fail("decoded sound for a text-only model"))
    indexer.sync(Selection(files=(str(path),)))
    indexer.run(FakeEmbedder())
    assert Searcher(store).search("Skylarks", None)[0].segment == "tag"
    assert store.vector_count() == 0
    store.close()


def test_partial_music_matches_cannot_displace_full_document_matches(tmp_path):
    store = Store(tmp_path / "index.sqlite")
    for i in range(150):
        file = store.add_file(str(tmp_path / f"song-{i}.mp3"), Kind.AUDIO, 100, 1)
        store.add_segments(file, [NewSegment(Segment.TAG, Modality.NONE, body="title: project")])
        store.finish(file, "done")
    document = str(tmp_path / "notes.txt")
    file = store.add_file(document, Kind.DOC, 100, 1)
    store.add_segments(file, [NewSegment(Segment.CHUNK, Modality.NONE, body="project release")])
    store.finish(file, "done")
    searcher = Searcher(store)
    assert searcher.search("project release", None)[0].path == document
    assert all(hit.kind is Kind.DOC for hit in searcher.search("project", None, kinds={Kind.DOC}))
    store.close()


def test_tag_text_uses_normal_text_scores_and_respects_inactive_files(tmp_path):
    store = Store(tmp_path / "index.sqlite")
    for kind, segment, name in ((Kind.AUDIO, Segment.TAG, "song.mp3"), (Kind.DOC, Segment.CHUNK, "notes.txt")):
        file = store.add_file(str(tmp_path / name), kind, 100, 1)
        store.add_segments(file, [NewSegment(segment, Modality.NONE, body="Midnight Lantern")])
        store.finish(file, "done")
    hits = Searcher(store).search("Midnight Lantern", None)
    assert len(hits) == 2 and hits[0].score == hits[1].score
    assert any(e["source"] == "tag" for e in hits[0].evidence)
    store.db.execute("UPDATE files SET active=0 WHERE kind='audio'")
    assert all(hit.kind is Kind.DOC for hit in Searcher(store).search("Midnight Lantern", None))
    store.close()
