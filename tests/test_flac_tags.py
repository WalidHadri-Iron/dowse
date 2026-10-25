"""FLAC Vorbis comments: bounded extraction, indexing and truthful search evidence."""

from dowse.extract import music_tags as flac_tags
from dowse.indexer import Indexer
from dowse.reading import Options, steps
from dowse.rules import Kind
from dowse.searcher import Searcher
from dowse.selection import Selection
from dowse.store import FileRow, Segment, Store
from test_index import FakeEmbedder
from test_media import write_audio
from music_fixtures import flac as tagged, block, comments


def test_read_flac_comments_and_ignore_picture_unknown_fields(tmp_path):
    path = tagged(tmp_path / "song.flac")
    assert flac_tags.read(str(path)) == {
        "title": "Starlight Drive", "artist": "The Night Owls; Second Artist", "album": "Evening Ride",
        "album_artist": "Night Studio", "track": "2", "date": "2025", "genre": "Jazz",
    }


def test_missing_truncated_invalid_and_oversized_comments_are_ignored(tmp_path):
    path = tmp_path / "bad.flac"
    path.write_bytes(b"not FLAC")
    assert flac_tags.read(str(path)) == {}
    path.write_bytes(b"fLaC" + block(4, b"\xff\xff\xff\xff", last=True))
    assert flac_tags.read(str(path)) == {}
    path.write_bytes(b"fLaC" + block(4, comments("TITLE=short"), last=True)[:-3])
    assert flac_tags.read(str(path)) == {}
    # A header advertising more than the read budget is rejected before reading the body.
    path.write_bytes(b"fLaC" + bytes([0x84]) + (flac_tags.MAX_READ_BYTES + 1).to_bytes(3, "big") +
                     b"\0" * (flac_tags.MAX_READ_BYTES + 1))
    assert flac_tags.read(str(path)) == {}


def test_flac_tags_are_keyword_searchable_and_identified_in_evidence(tmp_path):
    path = tagged(tmp_path / "song.flac")
    file = FileRow(1, str(path), Kind.AUDIO, path.stat().st_size, 1, "pending", {})
    step, = list(steps(file, Options(embed_media=False)))
    assert step.info["reader"] == 3 and step.info["music_tags"]["title"] == "Starlight Drive"
    assert [piece.kind for piece in step.pieces] == [Segment.TAG]
    assert step.pieces[0].loc is None

    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, Options(embed_media=False))
    indexer.sync(Selection(files=(str(path),)))
    indexer.run(FakeEmbedder())
    saved = store.files("kind = 'audio'")[0]
    assert saved.info["music_tags"]["artist"] == "The Night Owls; Second Artist"
    hit = Searcher(store).search("Starlight", None)[0]
    assert hit.path == str(path) and hit.segment == "tag" and hit.loc is None
    assert any(e["source"] == "tag" and "Starlight" in e["words"] for e in hit.evidence)
    store.close()


def test_playable_flac_indexes_tags_with_audio_on_first_step_only(tmp_path):
    path = tmp_path / "song.flac"
    write_audio(path, 6, codec="flac")
    tagged(path)

    options = Options(audio_window=2, audio_hop=2, media_step=2)
    file = FileRow(1, str(path), Kind.AUDIO, path.stat().st_size, 1, "pending", {})
    first = next(steps(file, options))
    assert first.info["music_tags"]["artist"] == "The Night Owls; Second Artist"
    assert [piece.kind for piece in first.pieces] == [Segment.TAG, Segment.AUDIO]
    assert first.position == 2
    assert all(piece.kind is not Segment.TAG for step in steps(file, options, start=2) for piece in step.pieces)

    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, options)
    indexer.sync(Selection(files=(str(path),)))
    indexer.run(FakeEmbedder())
    saved = store.files("kind = 'audio'")[0]
    assert saved.state == "done" and saved.info["music_tags"]["title"] == "Starlight Drive"
    assert store.db.execute("SELECT COUNT(*) FROM segments WHERE kind = 'tag'").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM segments WHERE kind = 'audio'").fetchone()[0] >= 1
    assert any(e["source"] == "tag" for e in Searcher(store).search("Starlight", None)[0].evidence)
    store.close()


def test_music_tag_evidence_remains_when_sound_is_the_selected_segment(tmp_path):
    from dowse.searcher import Hit
    from dowse.store import Modality, NewSegment

    store = Store(tmp_path / "index.sqlite")
    path = str(tmp_path / "song.flac")
    file = store.add_file(path, Kind.AUDIO, 100, 1)
    store.add_segments(file, [NewSegment(Segment.TAG, Modality.NONE, body="artist: The Night Owls"),
                              NewSegment(Segment.AUDIO, Modality.NONE, loc=0, loc_end=30, body="")])
    store.finish(file, "done")
    segments = dict(store.db.execute("SELECT kind, id FROM segments WHERE file = ?", (file,)))
    from dowse.searcher import _Words
    keyword = _Words(1, segments["tag"], patterns=['"owls"'])
    hit = Hit(path, Kind.AUDIO, 1, 2, .4, 1, "audio", 0, 30, None, 1, 100, {}, segment_id=segments["audio"])
    evidence = Searcher(store)._evidence(file, hit, keyword, semantic=True)
    assert any(e["source"] == "tag" and e["words"] == ["Owls"] for e in evidence)
    assert any(e["source"] == "semantic" and e["modality"] == "audio" for e in evidence)
    store.close()
