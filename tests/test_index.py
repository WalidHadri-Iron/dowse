"""The store, sync and search with a stand-in embedder (no model needed): vectors are made up, the logic is real."""

import base64
import io
import os
import time
import zlib

import numpy as np
import pytest
from PIL import Image

from dowse.embedder import DIM, target_size
from dowse.indexer import Indexer
from dowse.rules import Kind
from dowse.searcher import Ranking, Searcher, is_lookup, meaning, sidak
from dowse.selection import Selection
from dowse.store import Store


def unit(seed: int) -> np.ndarray:
    vector = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
    return vector / np.linalg.norm(vector)


def word_vector(word: str) -> np.ndarray:
    """A word's stand-in vector: the same in every run (crc32, not Python's per-process hash), and no two words
    share one (a small seed space did: "Eclipse" and "Page" met in 1 run out of ~100)."""
    return unit(zlib.crc32(word.encode()))


class FakeEmbedder:
    """Stand-in vectors: a text's from its first word (`word_vector`), a picture's from its top-left pixel, a
    sound's from its bytes.
    Counts what it embeds, so tests can see work done (and not done twice)."""

    def __init__(self):
        self.embedded = 0

    def texts(self, texts):
        self.embedded += len(texts)
        return np.stack([word_vector(t.split("text: ")[-1].split()[0]) for t in texts])

    def media(self, parts):
        self.embedded += len(parts)
        out = []
        for part in parts:
            content = part["content"][0]
            if content["type"] == "image_url":
                data = base64.b64decode(content["image_url"]["url"].split(",", 1)[1])
                out.append(unit(Image.open(io.BytesIO(data)).convert("RGB").getpixel((0, 0))[0]))
            else:
                out.append(unit(zlib.crc32(content["input_audio"]["data"].encode()) % 10_000))
        return out


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "files"
    (root / "photos").mkdir(parents=True)
    (root / "notes").mkdir()
    Image.new("RGB", (400, 300), (7, 0, 0)).save(root / "photos" / "IMG_1.png")   # lossless: its pixel is its vector
    Image.new("RGB", (400, 300), (9, 0, 0)).save(root / "photos" / "IMG_2.png")
    Image.new("RGB", (40, 30)).save(root / "photos" / "tiny.png")
    # Meaning scores are z-scores: with a handful of vectors none can stand out (n vectors cap z at √(n − 1)).
    (root / "filler").mkdir()
    for shade in range(20, 50):
        Image.new("RGB", (300, 300), (shade, 0, 0)).save(root / "filler" / f"DSC_{shade}.png")
    (root / "notes" / "rent.txt").write_text("lease terms: the rent is 1,200 euros, invoice INV-2026-0042.")
    (root / "notes" / "cake.txt").write_text("banana bread with walnuts and cinnamon")
    return root


def test_index_and_search(tmp_path, folder):
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, workers=2)
    summary = indexer.sync(Selection((str(folder),)))
    assert (summary.added, summary.found) == (35, 35)
    assert indexer.run(FakeEmbedder()) == 35
    counts = store.counts()
    assert counts["image"] == {"done": 32, "failed": 1}   # tiny.png is too small
    assert counts["doc"] == {"done": 2}

    searcher = Searcher(store)
    assert len(searcher.matrix) == 34
    # A picture found by its vector.
    hits = searcher.search("", unit(7))
    assert hits[0].path.endswith("IMG_1.png") and hits[0].z > 1
    # A code found by its words, with the words marked.
    hits = searcher.search("INV-2026-0042", None)
    assert hits[0].path.endswith("rent.txt") and "«" in hits[0].excerpt
    # Kinds filter.
    assert all(h.kind is Kind.DOC for h in searcher.search("banana", unit(7), kinds={Kind.DOC}))


def test_sync_moves_changes_and_removals(tmp_path, folder):
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, workers=2)
    indexer.sync(Selection((str(folder),)))
    indexer.run(FakeEmbedder())
    vectors = store.vector_count()

    os.replace(folder / "notes" / "cake.txt", folder / "photos" / "cake.txt")      # moved
    (folder / "notes" / "rent.txt").write_text("changed text")                     # changed
    future = time.time() + 10
    os.utime(folder / "notes" / "rent.txt", (future, future))
    (folder / "photos" / "IMG_2.png").unlink()                                     # gone
    summary = indexer.sync(Selection((str(folder),)))
    assert (summary.moved, summary.changed, summary.removed, summary.added) == (1, 1, 1, 0)
    # The moved file keeps its vector; the gone one's vector went with it (cascade + FTS trigger).
    assert store.vector_count() == vectors - 2   # IMG_2's and rent.txt's (pending again)
    assert store.db.execute("SELECT COUNT(*) FROM fts").fetchone()[0] == \
        store.db.execute("SELECT COUNT(*) FROM segments WHERE id IN (SELECT rowid FROM fts)").fetchone()[0]
    assert [f.path for f in store.pending()] == [str(folder / "notes" / "rent.txt")]


def test_meaning_scale():
    rng = np.random.default_rng(0)
    scores = rng.normal(0.5, 0.05, 1000).astype(np.float32)
    scores[:500] += 0.1                       # text vectors sit higher than image vectors for every query
    modality = np.array(["text"] * 500 + ["image"] * 500)
    z = meaning(scores, modality)
    assert abs(z[:500].mean() - z[500:].mean()) < 0.05   # put on one scale


def test_lookup():
    assert is_lookup(["k7q2lm"]) and is_lookup(["4815162342"]) and not is_lookup(["dog"]) and not is_lookup(["mp3", "dog"])


def test_target_size_matches_the_reference_processor():
    assert target_size(768, 1024, 140) == (480, 624)   # 10 × 13 = 130 tokens
    assert target_size(900, 1440, 280) == (624, 1008)  # 13 × 21 = 273 tokens
    assert target_size(4000, 10, 280) is not None


# Long files: read a step at a time, resumable.

def long_files(root):
    """A 70-page PDF, a 120-passage document and a 3-minute recording: all longer than one step."""
    from fpdf import FPDF

    from test_media import write_audio

    root.mkdir(parents=True, exist_ok=True)
    document = FPDF()
    for page in range(70):
        document.add_page()
        document.set_font("helvetica", size=12)
        opening = "Eclipse" if page % 10 == 0 else "Page"   # 7 pages about something: they share a fake vector
        document.multi_cell(0, 7, f"{opening} {page + 1}. " + " ".join(f"word{page}x{i}" for i in range(60)))
    document.output(str(root / "book.pdf"))
    (root / "notes.txt").write_text("\n\n".join(f"Paragraph {i}: " + "lorem ipsum dolor " * 100 for i in range(120)))
    write_audio(root / "talk.mp3", 180, codec="libmp3lame")
    return root


SMALL_STEPS = dict(pdf_step=30, doc_step=40, media_step=50.0)


def segments(store: Store) -> list[tuple]:
    return sorted(store.db.execute(
        "SELECT f.path, s.kind, s.loc, s.loc_end, s.vec IS NOT NULL FROM segments s JOIN files f ON f.id = s.file "
        "WHERE s.kind != 'name'"))


def test_long_files_first_step_then_the_rest(tmp_path):
    from dowse.reading import Options

    root = long_files(tmp_path / "long")
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, Options(**SMALL_STEPS), workers=2)
    indexer.sync(Selection((str(root),)))
    states = []
    indexer.run(FakeEmbedder(), on_progress=lambda p: states.append((os.path.basename(p.path), p.state, p.total)))
    first = [s for s in states if s[2]]          # the first pass reports a total; the later pass doesn't
    assert sorted(first) == [("book.pdf", "partial", 3), ("notes.txt", "partial", 3), ("talk.mp3", "partial", 3)]
    assert all(f.state == "done" and "read_to" not in f.info for f in store.files())
    pages = store.db.execute("SELECT COUNT(*) FROM segments WHERE kind = 'page'").fetchone()[0]
    windows = [tuple(r) for r in store.db.execute("SELECT loc, loc_end FROM segments WHERE kind = 'audio' ORDER BY loc")]
    assert pages == 70 and windows == [(t, t + 30) for t in range(0, 151, 25)][:-1] + [(150, 180)]
    assert store.db.execute("SELECT COUNT(*) FROM segments WHERE kind = 'chunk'").fetchone()[0] > 80


def test_a_stopped_run_resumes_where_it_was(tmp_path):
    from dowse.reading import Options

    root = long_files(tmp_path / "long")
    whole = Store(tmp_path / "whole.sqlite")
    Indexer(whole, Options(**SMALL_STEPS), workers=2).sync(Selection((str(root),)))
    full = FakeEmbedder()
    Indexer(whole, Options(**SMALL_STEPS), workers=2).run(full)

    store = Store(tmp_path / "stopped.sqlite")
    indexer = Indexer(store, Options(**SMALL_STEPS), workers=2)
    indexer.sync(Selection((str(root),)))
    embedder, later_steps = FakeEmbedder(), []

    def stop_after_two_later_steps():
        return len(later_steps) >= 2

    indexer.run(embedder, should_stop=stop_after_two_later_steps,
                on_progress=lambda p: later_steps.append(p) if not p.total else None)
    assert any(f.state == "partial" for f in store.files())
    resumed = FakeEmbedder()
    indexer.run(resumed)
    assert all(f.state == "done" for f in store.files())
    assert segments(store) == segments(whole)                          # the same index as an uninterrupted run
    assert embedder.embedded + resumed.embedded == full.embedded       # and nothing was embedded twice



def test_every_run_moves_forward_however_often_it_is_stopped(tmp_path):
    """Changes that keep arriving stop every run at once: each must still save what it read, and the runs add up
    to the same index as one uninterrupted run."""
    from dowse.reading import Options

    root = long_files(tmp_path / "long")
    whole = Store(tmp_path / "whole.sqlite")
    Indexer(whole, Options(**SMALL_STEPS), workers=2).sync(Selection((str(root),)))
    Indexer(whole, Options(**SMALL_STEPS), workers=2).run(FakeEmbedder())

    store = Store(tmp_path / "busy.sqlite")
    indexer = Indexer(store, Options(**SMALL_STEPS), workers=2)
    indexer.sync(Selection((str(root),)))

    def unfinished() -> int:
        return sum(f.state in ("pending", "partial") for f in store.files())

    runs = 0
    while unfinished() and runs < 50:
        before = segments(store)
        indexer.run(FakeEmbedder(), should_stop=lambda: True)
        assert segments(store) != before                               # something was saved, every time
        runs += 1
    assert not unfinished() and segments(store) == segments(whole)


# Ranking corrections

def test_sidak_puts_a_best_of_n_back_on_a_one_draw_scale():
    assert sidak(3.0, 1) == 3.0
    assert sidak(3.92, 470) == pytest.approx(2.04, abs=0.02)      # a book's best passage, by chance
    assert sidak(6.0, 470) == pytest.approx(4.9, abs=0.1)         # a passage that truly matches stays far out
    assert sidak(-1.0, 50) < 2 and sidak(40.0, 1000) > 30          # no overflow at either end


def test_words_must_be_found_together(tmp_path):
    """A long file holding each word somewhere loses to a short one holding them in one place."""
    root = tmp_path / "files"
    root.mkdir()
    scattered = ["the red car was parked"] + ["filler text about nothing"] * 10 + ["a quiet garden"]
    (root / "long.txt").write_text("\n\n".join(p + " " + "padding " * 300 for p in scattered))
    (root / "short.txt").write_text("a red garden full of roses")
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, workers=2)
    indexer.sync(Selection((str(root),)))
    indexer.run(FakeEmbedder())
    together = Searcher(store).search("red garden", None)
    assert os.path.basename(together[0].path) == "short.txt"
    anywhere = Searcher(store, Ranking(rare_words=False)).search("red garden", None)
    assert {os.path.basename(h.path) for h in anywhere[:2]} == {"short.txt", "long.txt"}
    assert anywhere[0].score == anywhere[1].score   # words anywhere in the file can't tell them apart


def test_hubs_are_demoted(tmp_path, folder):
    """A vector close to every probe (a hub) stands out less; a real match stays first."""
    import base64 as b64

    from dowse import probes as probe_bank

    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, workers=2)
    indexer.sync(Selection((str(folder),)))
    indexer.run(FakeEmbedder())
    bank = np.stack([unit(1000 + i) for i in range(len(probe_bank.PROBES))]).astype(np.float32)
    store.set_meta("probes", f"{len(bank)}:" + b64.b64encode(bank.tobytes()).decode())
    centroid = bank.mean(axis=0) / np.linalg.norm(bank.mean(axis=0))
    hub = 0.5 * unit(9) + 0.85 * centroid                     # IMG_2 moves toward the probes: a hub
    store.db.execute("UPDATE segments SET vec = ? WHERE kind = 'image' AND file = "
                     "(SELECT id FROM files WHERE path LIKE '%IMG_2.png')",
                     ((hub / np.linalg.norm(hub)).astype("<f2").tobytes(),))
    query = 0.8 * unit(7) + 0.6 * centroid                    # matches IMG_1, and leans toward the probes too
    query /= np.linalg.norm(query)

    def z_of(hits, name):
        return next(h.z for h in hits if h.path.endswith(name))

    plain = Searcher(store, Ranking(hub=0)).search("", query, z_gate=float("-inf"))
    corrected = Searcher(store).search("", query, z_gate=float("-inf"))
    assert corrected[0].path.endswith("IMG_1.png") and plain[0].path.endswith("IMG_1.png")
    assert z_of(corrected, "IMG_2.png") < z_of(plain, "IMG_2.png")               # the hub stands out less
    assert z_of(corrected, "IMG_1.png") - z_of(corrected, "IMG_2.png") > \
        z_of(plain, "IMG_1.png") - z_of(plain, "IMG_2.png")                          # and falls behind the match


def test_long_files_show_more_moments(tmp_path):
    from dowse.reading import Options

    root = long_files(tmp_path / "long")
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, Options(**SMALL_STEPS), workers=2)
    indexer.sync(Selection((str(root),)))
    indexer.run(FakeEmbedder())
    # The 7 "Eclipse" pages share one fake vector: the query matches them, and only them.
    hits = Searcher(store).search("", word_vector("Eclipse"))
    book = next((h for h in hits if h.path.endswith("book.pdf")), None)
    assert book is not None, ([(os.path.basename(h.path), h.z) for h in hits],
                              [(os.path.basename(f.path), f.state, f.info.get("read_to")) for f in store.files()],
                              store.db.execute("SELECT kind, COUNT(*) FROM segments GROUP BY kind").fetchall())
    assert book.loc is not None and len(book.moments) == 3
    assert len({book.loc, *(loc for loc, _ in book.moments)}) == 4   # four different pages
