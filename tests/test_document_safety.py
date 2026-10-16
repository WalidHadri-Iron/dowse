"""Compressed documents have one bounded read budget, including their metadata."""

import io
import zipfile
from pathlib import Path

import pytest

from dowse.extract import ExtractError, docs


def archive_at(path, members):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return str(path)


@pytest.mark.parametrize("extension, member", [
    ("docx", "word/document.xml"), ("pptx", "ppt/slides/slide1.xml"),
    ("odt", "content.xml"), ("odp", "content.xml"), ("ods", "content.xml"),
    ("xlsx", "xl/sharedStrings.xml"), ("epub", "META-INF/container.xml"),
])
def test_oversized_member_is_refused_before_decompression(tmp_path, monkeypatch, extension, member):
    monkeypatch.setattr(docs, "MAX_BYTES", 512)
    path = archive_at(tmp_path / f"large.{extension}", {member: "x" * 513})

    def never_open(*args, **kwargs):
        pytest.fail("oversized content must be refused before opening/decompressing it")

    monkeypatch.setattr(zipfile.ZipFile, "open", never_open)
    with pytest.raises(ExtractError, match="decompressed content"):
        docs.read(path)


def test_real_small_archive_over_the_production_limit_is_refused(tmp_path):
    path = tmp_path / "compressed.docx"
    chunk = b"x" * (1 << 20)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        with archive.open("word/document.xml", "w") as content:
            for _ in range(65):
                content.write(chunk)
    assert path.stat().st_size < 100_000   # a tiny file must not expand into an unchecked allocation
    with pytest.raises(ExtractError, match="decompressed content"):
        docs.read(str(path))


TITLE = '<core><title>Budget</title></core>'
PARAGRAPH = '<root><p><t>hello</t></p></root>'


def document_members(extension):
    if extension == "docx":
        return {"word/document.xml": PARAGRAPH, "docProps/core.xml": TITLE}
    if extension == "pptx":
        return {"ppt/slides/slide1.xml": PARAGRAPH, "ppt/slides/slide2.xml": PARAGRAPH,
                "docProps/core.xml": TITLE}
    if extension == "xlsx":
        sheet = '<worksheet><row><c t="s"><v>0</v></c></row></worksheet>'
        return {
            "xl/sharedStrings.xml": '<sst><si><t>hello</t></si></sst>',
            "xl/workbook.xml": '<workbook xmlns:r="urn:rel"><sheets><sheet name="One" r:id="a"/>'
                               '<sheet name="Two" r:id="b"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": '<rels><rel Id="a" Target="worksheets/one.xml"/>'
                                         '<rel Id="b" Target="worksheets/two.xml"/></rels>',
            "xl/worksheets/one.xml": sheet, "xl/worksheets/two.xml": sheet,
            "docProps/core.xml": TITLE,
        }
    return {
        "META-INF/container.xml": '<container><rootfile full-path="OPS/book.opf"/></container>',
        "OPS/book.opf": '<package><title>Budget</title><manifest><item id="a" href="one.xhtml"/>'
                        '<item id="b" href="two.xhtml"/></manifest><spine><itemref idref="a"/>'
                        '<itemref idref="b"/></spine></package>',
        "OPS/one.xhtml": '<p>hello</p>', "OPS/two.xhtml": '<p>hello again</p>',
    }


@pytest.mark.parametrize("extension", ["docx", "pptx", "xlsx", "epub"])
def test_all_parts_and_metadata_share_one_budget(tmp_path, monkeypatch, extension):
    members = document_members(extension)
    total = sum(len(data.encode()) for data in members.values())
    # A large unused image must not consume the text budget or be decompressed.
    path = archive_at(tmp_path / f"normal.{extension}", {**members, "media/photo.png": "x" * (total * 2)})
    monkeypatch.setattr(docs, "MAX_BYTES", total)
    text, title = docs.read(path)
    assert "hello" in text and title == "Budget"

    monkeypatch.setattr(docs, "MAX_BYTES", total - 1)
    assert max(len(data.encode()) for data in members.values()) < docs.MAX_BYTES
    with pytest.raises(ExtractError, match="decompressed content"):
        docs.read(path)


def test_actual_read_is_bounded_even_if_declared_size_is_wrong(tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "MAX_BYTES", 8)
    path = archive_at(tmp_path / "small.docx", {"word/document.xml": "x"})
    sizes = []

    class Reader(io.BytesIO):
        def read(self, size=-1):
            sizes.append(size)
            assert size == 9   # budget plus one sentinel byte, never an unbounded read
            return super().read(size)

    with docs._Archive(path) as archive:
        monkeypatch.setattr(archive, "open", lambda *args, **kwargs: Reader(b"x" * 20))
        with pytest.raises(ExtractError, match="decompressed content"):
            archive.read("word/document.xml")
    assert sizes == [9]


def test_plain_documents_keep_the_same_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "MAX_BYTES", 8)
    path = tmp_path / "notes.txt"
    path.write_bytes(b"hello!!!")
    assert docs.read(str(path)) == ("hello!!!", None)
    path.write_bytes(b"hello!!!!")
    with pytest.raises(ExtractError, match="document over"):
        docs.read(str(path))


def test_oversized_document_fails_only_that_file_and_indexing_continues(tmp_path, monkeypatch):
    from dowse.indexer import Indexer
    from dowse.selection import Selection
    from dowse.store import Store
    from test_index import FakeEmbedder

    monkeypatch.setattr(docs, "MAX_BYTES", 512)
    folder = tmp_path / "files"
    folder.mkdir()
    archive_at(folder / "large.docx", {"word/document.xml": "x" * 513})
    (folder / "notes.txt").write_text("a normal document about engineering", encoding="utf-8")
    store = Store(tmp_path / "index.sqlite")
    states = []
    try:
        indexer = Indexer(store, workers=2)
        indexer.sync(Selection((str(folder),)))
        indexer.run(FakeEmbedder(), on_progress=lambda p: states.append((p.state, p.detail)))
        assert {Path(file.path).name: file.state for file in store.files()} == {
            "large.docx": "failed", "notes.txt": "done"}
        assert any(state == "failed" and "decompressed content" in detail for state, detail in states)
        assert not store.pending()
    finally:
        store.close()
