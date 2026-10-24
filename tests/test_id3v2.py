"""ID3v2 text tags: bounded extraction and searchable, untimed music metadata."""

from mutagen.id3 import ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK

from dowse.extract import id3v2
from dowse.indexer import Indexer
from dowse.reading import Options, steps
from dowse.rules import Kind
from dowse.searcher import Searcher
from dowse.selection import Selection
from dowse.store import FileRow, Modality, NewSegment, Segment, Store
from test_index import FakeEmbedder
from test_media import write_audio


def tagged(path):
    """A header-only ID3v2 file is enough for metadata tests; PyAV needs sound for the full index test."""
    tags = ID3()
    for frame in (TIT2(encoding=3, text="Midnight Lantern"), TPE1(encoding=3, text="The Skylarks"),
                  TALB(encoding=3, text="Winter Stories"), TPE2(encoding=3, text="Northern Band"),
                  TRCK(encoding=3, text="4/12"), TDRC(encoding=3, text="2024"),
                  TCON(encoding=3, text="Folk")):
        tags.add(frame)
    tags.save(path, v2_version=3)
    return path


def test_reads_selected_id3v2_text_only(tmp_path):
    path = tagged(tmp_path / "song.mp3")
    assert id3v2.read(str(path)) == {
        "title": "Midnight Lantern", "artist": "The Skylarks", "album": "Winter Stories",
        "album_artist": "Northern Band", "track": "4/12", "date": "2024", "genre": "Folk",
    }


def test_missing_broken_and_oversized_tags_are_ignored(tmp_path):
    path = tmp_path / "song.mp3"
    path.write_bytes(b"sound without ID3")
    assert id3v2.read(str(path)) == {}
    path.write_bytes(b"ID3\x03\x00\x00\x7f\x7f\x7f\x7f")
    assert id3v2.read(str(path)) == {}  # no unbounded artwork read
    path.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x20bad")
    assert id3v2.read(str(path)) == {}
    assert id3v2.read(str(tmp_path / "absent.mp3")) == {}


def test_text_only_reader_indexes_tags_once_without_decoding_sound(tmp_path):
    path = tagged(tmp_path / "song.mp3")
    file = FileRow(1, str(path), Kind.AUDIO, path.stat().st_size, 1, "pending", {})
    result = list(steps(file, Options(embed_media=False)))
    assert len(result) == 1 and result[0].keyword_only and result[0].info["reader"] == 3
    assert result[0].info["id3v2"]["artist"] == "The Skylarks"
    assert len(result[0].pieces) == 1 and result[0].pieces[0].kind is Segment.TAG
    assert "Midnight Lantern" in result[0].pieces[0].body
    assert result[0].pieces[0].loc is None


def test_tags_are_persisted_and_found_by_keyword_search(tmp_path):
    path = tagged(tmp_path / "song.mp3")
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, Options(embed_media=False))
    indexer.sync(Selection(files=(str(path),)))
    indexer.run(FakeEmbedder())
    file = store.files("kind = 'audio'")[0]
    assert file.info["id3v2"]["title"] == "Midnight Lantern"
    assert store.db.execute("SELECT COUNT(*) FROM segments WHERE file = ? AND kind = 'tag'", (file.id,)).fetchone()[0] == 1
    hits = Searcher(store).search("Skylarks", None)
    assert hits and hits[0].path == str(path) and hits[0].loc is None
    assert any(e["source"] == "tag" and "Skylarks" in e["words"] for e in hits[0].evidence)
    store.close()


def test_tag_artist_is_not_lost_among_folder_name_matches(tmp_path):
    path = tagged(tmp_path / "song.mp3")
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, Options(embed_media=False))
    indexer.sync(Selection(files=(str(path),)))
    indexer.run(FakeEmbedder())
    # A large collection can have more than 100 files in an artist-named folder.
    # Their names used to exhaust keyword retrieval before the song's tag was read.
    for i in range(120):
        file_id = store.add_file(str(tmp_path / "The Skylarks" / f"track-{i}.wav"), Kind.AUDIO, 100, 1)
        store.add_segments(file_id, [NewSegment(Segment.NAME, Modality.NONE, name="The Skylarks track")])
        store.finish(file_id, "done")
    hits = Searcher(store).search("Skylarks", None, limit=10)
    assert hits and hits[0].path == str(path) and hits[0].segment == "tag"
    store.close()


def test_mp3_keeps_sound_and_indexes_tags_only_on_first_step(tmp_path):
    path = tmp_path / "song.mp3"
    write_audio(path, 6, codec="libmp3lame")
    tagged(path)
    file = FileRow(1, str(path), Kind.AUDIO, path.stat().st_size, 1, "pending", {})
    options = Options(audio_window=2, audio_hop=2, media_step=2)
    first = next(steps(file, options))
    assert first.info["id3v2"]["title"] == "Midnight Lantern"
    assert [piece.kind for piece in first.pieces] == [Segment.TAG, Segment.AUDIO]
    assert first.position == 2
    later = list(steps(file, options, start=first.position))
    assert later and all(piece.kind is not Segment.TAG for step in later for piece in step.pieces)

    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, options)
    indexer.sync(Selection(files=(str(path),)))
    indexer.run(FakeEmbedder())
    saved = store.files("kind = 'audio'")[0]
    assert saved.state == "done" and saved.info["id3v2"]["artist"] == "The Skylarks"
    assert store.db.execute("SELECT COUNT(*) FROM segments WHERE kind = 'tag'").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM segments WHERE kind = 'audio'").fetchone()[0] >= 1
    assert Searcher(store).search("Lantern", None)[0].path == str(path)
    store.close()