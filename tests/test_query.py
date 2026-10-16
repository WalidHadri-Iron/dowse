"""What a query says besides its meaning (a date, a kind, a misspelt name), what reading keeps for search (titles
from names, photos' words, more kinds of document), and files read before a reader changed being read again."""

import os
import zipfile
from datetime import datetime

import numpy as np
import pytest
from PIL import Image

from dowse import dates, reading
from dowse.extract import docs
from dowse.indexer import Indexer
from dowse.reading import title_of
from dowse.rules import Kind
from dowse.searcher import Searcher, for_meaning, kind_filter, spelling_distance, strong_count
from dowse.selection import Selection
from dowse.store import Store
from test_index import FakeEmbedder, unit

NOW = datetime(2026, 10, 13, 12).timestamp()


def day(stamp: float) -> str:
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")


@pytest.mark.parametrize("query, start, end, rest", [
    ("sunflowers from last summer", "2026-06-01", "2026-09-01", "sunflowers"),
    ("snowy mountain from last winter", "2025-12-01", "2026-03-01", "snowy mountain"),
    ("sunflower photo from July 2025", "2025-07-01", "2025-08-01", "sunflower photo"),
    ("photos taken in 2025", "2025-01-01", "2026-01-01", "photos"),
    ("standup notes from last week", "2026-10-05", "2026-10-12", "standup notes"),
    ("standup notes from August", "2026-08-01", "2026-09-01", "standup notes"),
    ("email from yesterday about the rent", "2026-10-12", "2026-10-13", "email about the rent"),
    ("3 weeks ago meeting notes", "2026-09-21", "2026-09-28", "meeting notes"),
    ("photos since March", "2026-03-01", "2026-10-14", "photos"),
])
def test_dates_said_plainly(query, start, end, rest):
    span = dates.parse(query, NOW)
    assert (day(span.start), day(span.end), span.rest) == (start, end, rest)
    assert for_meaning(query, now=NOW) == rest


@pytest.mark.parametrize("query", ["tax return 2019", "may I see the lease", "summer dress", "pride and prejudice",
                                   "a song from 2099"])
def test_what_isnt_a_date_stays_words(query):
    assert dates.parse(query, NOW) is None and for_meaning(query, now=NOW) == query


def test_kinds_said_plainly_filter():
    assert kind_filter("photos of snow") == {Kind.IMAGE}
    assert kind_filter("the pdf about renting") == {Kind.PDF}
    assert kind_filter("a screen recording of the bug") == {Kind.VIDEO}
    assert kind_filter("doctor's note fit for work") == set()   # "note" only leans: it may well be a PDF
    assert kind_filter("zebra in a video") == {Kind.VIDEO}
    assert kind_filter("wedding photos") == {Kind.IMAGE}
    assert kind_filter("script that shrinks photos for email") == set()   # what it's about, not what it is
    assert kind_filter("a song by the Beatles") == set()


def test_spelling_distance():
    assert spelling_distance("sherlok", "sherlock", 2) == 1
    assert spelling_distance("lighthose", "lighthouse", 2) == 1
    assert spelling_distance("contcts", "contacts", 2) == 1
    assert spelling_distance("form", "from", 1) == 1      # two letters swapped: one edit
    assert spelling_distance("giraffe", "garage", 2) == 3  # too far: "most + 1"


def test_titles_from_names():
    assert title_of(r"C:\x\Grandma's soup.txt", None) == "Grandma's soup"
    assert title_of(r"C:\x\Cover letter - Lumen Labs.docx", None) == "Cover letter Lumen Labs"
    assert title_of(r"C:\x\Q3 budget.docx", "Minutes of the Q3 meeting") == "Minutes of the Q3 meeting"
    for nameless in ("IMG_1001.jpg", "scan_0042.pdf", "doc_scan_001.pdf", "2025-02-05 11-04-13.txt",
                     "new document (2).docx"):
        assert title_of(rf"C:\x\{nameless}", None) is None
    assert title_of(r"C:\x\a.docx", "Microsoft Word - a.docx") is None


def test_more_kinds_of_documents(tmp_path):
    book = tmp_path / "book.xlsx"   # shared strings, as Excel writes them
    with zipfile.ZipFile(book, "w") as archive:
        main = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        archive.writestr("xl/workbook.xml", f'<workbook {main} xmlns:r="{rel}"><sheets><sheet name="Costs" '
                         'sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/'
                         f'package/2006/relationships"><Relationship Id="rId1" Type="{rel}/worksheet" '
                         'Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/sharedStrings.xml", f'<sst {main}><si><t>Car insurance</t></si><si><t>Gym</t></si></sst>')
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet {main}><sheetData><row r="1"><c r="A1" t="s"><v>0</v>'
                         '</c><c r="B1"><v>61</v></c></row><row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>29</v>'
                         '</c></row></sheetData></worksheet>')
    assert docs.read(str(book))[0] == "Sheet: Costs\nCar insurance | 61\nGym | 29"

    epub = tmp_path / "story.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                         '<rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        archive.writestr("OPS/book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl'
                         '.org/dc/elements/1.1/"><dc:title>The Keeper</dc:title></metadata><manifest><item id="b" '
                         'href="two.xhtml"/><item id="a" href="one.xhtml"/></manifest><spine><itemref idref="a"/>'
                         '<itemref idref="b"/></spine></package>')
        archive.writestr("OPS/one.xhtml", "<html><body><p>The lamp is lit.</p></body></html>")
        archive.writestr("OPS/two.xhtml", "<html><body><p>The storm comes.</p></body></html>")
    text, title = docs.read(str(epub))
    assert title == "The Keeper" and text.index("lamp") < text.index("storm")   # in the spine's order

    sheet = tmp_path / "plan.ods"
    with zipfile.ZipFile(sheet, "w") as archive:
        archive.writestr("content.xml", '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:'
                         'office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:'
                         'oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet><table:table '
                         'table:name="Trip"><table:table-row><table:table-cell><text:p>Lisbon</text:p></table:table-'
                         'cell><table:table-cell/><table:table-cell><text:p>3 days</text:p></table:table-cell>'
                         '</table:table-row></table:table></office:spreadsheet></office:body></office:document-content>')
    assert docs.read(str(sheet))[0] == "Sheet: Trip\nLisbon | 3 days"


def _index(tmp_path, root) -> tuple[Store, Indexer, FakeEmbedder]:
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(store, workers=2)
    indexer.sync(Selection((str(root),)))
    embedder = FakeEmbedder()
    indexer.run(embedder)
    return store, indexer, embedder


def test_a_date_or_a_kind_in_the_query_filters(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    for name, taken in [("IMG_1.jpg", "2026:07:20 15:30:00"), ("IMG_2.jpg", "2025:07:12 11:05:00")]:
        picture = Image.new("RGB", (400, 300), (7, 0, 0))
        exif = picture.getexif()
        exif.get_ifd(0x8769)[0x9003] = taken
        picture.save(root / name, quality=95, exif=exif)
    (root / "notes.txt").write_text("sunflowers in the garden")
    store, _, _ = _index(tmp_path, root)
    searcher = Searcher(store)
    names = [os.path.basename(h.path) for h in searcher.search("photos from last summer", unit(7), now=NOW)]
    assert names == ["IMG_1.jpg"]   # taken in July 2026 (not the file's date: both were written today)
    names = [os.path.basename(h.path) for h in searcher.search("photos taken in 2025", unit(7), now=NOW)]
    assert names == ["IMG_2.jpg"]
    assert {h.kind for h in searcher.search("photos of sunflowers", unit(7))} <= {Kind.IMAGE}
    assert searcher.search("sunflowers from last summer", None, now=NOW) == []   # the note is from today


def test_a_misspelt_name_is_found(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    (root / "sherlock holmes.txt").write_text("a detective story set in London")
    (root / "garage.txt").write_text("the car is serviced on Monday")
    store, _, _ = _index(tmp_path, root)
    searcher = Searcher(store)
    assert os.path.basename(searcher.search("sherlok", None)[0].path) == "sherlock holmes.txt"
    assert searcher.search("giraffe", None) == []   # no name near it: nothing


def test_files_read_before_a_reader_changed_are_read_again(tmp_path, monkeypatch):
    root = tmp_path / "files"
    root.mkdir()
    Image.new("RGB", (400, 300), (7, 0, 0)).save(root / "IMG_9.png")
    (root / "Grandma's soup.txt").write_text("two carrots, one onion, three potatoes")
    monkeypatch.setattr(reading.ocr, "text", lambda image: "Guest WiFi password olive-tree-42")
    store, indexer, embedder = _index(tmp_path, root)
    # As an older Dowse left them: no words for the photo, the note read without its name as a title
    store.db.execute("DELETE FROM segments WHERE kind = 'ocr'")
    store.db.execute("UPDATE files SET info = json_set(info, '$.reader', 1)")
    store.db.commit()
    assert not Searcher(store).search("olive", None)

    embedded = embedder.embedded
    indexer.sync(Selection((str(root),)))
    assert len(store.pending()) == 2
    vectors = store.vector_count()
    assert Searcher(store).search("carrots", None)   # still searchable while it waits
    indexer.run(embedder)
    assert embedder.embedded == embedded + 1      # the note's passage again; the photo's picture not again
    assert store.vector_count() == vectors
    hits = Searcher(store).search("olive", None)
    assert [os.path.basename(h.path) for h in hits] == ["IMG_9.png"]
    assert {f.info["reader"] for f in store.files("1 = 1")} == {2}
    indexer.sync(Selection((str(root),)))
    assert store.pending() == []   # read with today's readers: nothing more to do


def _toward(*parts: tuple[float, int]) -> np.ndarray:
    vector = sum(weight * unit(seed) for weight, seed in parts)
    return vector / np.linalg.norm(vector)


def test_nothing_clearly_matching_is_said(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    Image.new("RGB", (400, 300), (7, 0, 0)).save(root / "IMG_1.png")   # lossless: its pixel is its vector
    for shade in range(20, 50):   # enough vectors for one to stand out
        Image.new("RGB", (300, 300), (shade, 0, 0)).save(root / f"DSC_{shade}.png")
    (root / "cake.txt").write_text("banana bread with walnuts and cinnamon")
    store, _, _ = _index(tmp_path, root)
    searcher = Searcher(store)
    giraffe = _toward((1.0, 7), (0.9, 999))   # closest to IMG_1 (the "zebra"), but not about it
    searcher.bank = np.stack([_toward((1.0, 7), (0.2, 5)), _toward((1.0, 7), (0.2, 6)), unit(123)])
    hits = searcher.search("", giraffe)
    assert hits and os.path.basename(hits[0].path) == "IMG_1.png"
    assert not hits[0].clear and strong_count(hits) == 0   # two everyday queries describe it better
    searcher.bank = np.stack([giraffe, _toward((1.0, 7), (0.2, 5)), unit(123)])   # one, and the query itself
    hits = searcher.search("", giraffe)
    assert hits[0].clear and strong_count(hits) >= 1
    # Words only (the model asleep): found when all the words are
    assert strong_count(searcher.search("banana walnuts", None)) == 1
    assert strong_count(searcher.search("banana giraffe", None)) == 0
