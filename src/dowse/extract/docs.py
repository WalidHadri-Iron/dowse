"""Documents as plain text: txt/md/rst, HTML, Word (docx), OpenDocument (odt, odp, ods), PowerPoint (pptx), Excel
(xlsx), RTF, e-mail, e-books (epub), CSV and source code. A spreadsheet is its sheets' names and its rows, a cell
apart by " | "."""

import email
import email.policy
import re
import zipfile
from pathlib import PurePath
from xml.etree import ElementTree

from .. import searchtext
from ..rules import extension
from . import ExtractError

MAX_BYTES = 64 << 20
MAX_ROWS = 20_000   # of a spreadsheet's rows, all sheets together: past that it's data, not a document


class _Archive(zipfile.ZipFile):
    """One decompression budget for the whole document, including titles and other metadata.

    Check declared sizes before allocating, then bound the actual read too. Unused embedded images aren't read
    or charged: an illustrated document can still be searched without decompressing its pictures.
    """

    def __init__(self, path: str):
        super().__init__(path)
        self.left = MAX_BYTES

    def read(self, name, pwd=None) -> bytes:
        member = name if isinstance(name, zipfile.ZipInfo) else self.getinfo(name)
        if member.file_size > self.left:
            raise ExtractError("document's decompressed content over 64 MB")
        with self.open(member, pwd=pwd) as file:
            data = file.read(self.left + 1)
        if len(data) > self.left:
            raise ExtractError("document's decompressed content over 64 MB")
        self.left -= len(data)
        return data


def read(path: str) -> tuple[str, str | None]:
    """(text, title). Slides of a deck come one per paragraph, in order."""
    ext = extension(path)
    try:
        if ext in ("html", "htm"):
            raw = _decode(_bytes(path))
            title = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
            return searchtext.html_to_text(raw), (searchtext.html_to_text(title.group(1)) if title else None)
        if ext in ("docx", "pptx", "odt", "odp", "ods", "xlsx", "epub"):
            with _Archive(path) as archive:
                if ext == "docx":
                    return _xml_text(archive, ["word/document.xml"], "p"), _core_title(archive)
                if ext == "pptx":
                    slides = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                                    key=lambda n: int(re.search(r"\d+", PurePath(n).name).group()))
                    return _xml_text(archive, slides, "p"), _core_title(archive)
                if ext in ("odt", "odp"):
                    return _xml_text(archive, ["content.xml"], "p", "h"), None
                if ext == "ods":
                    return _ods_text(archive), None
                if ext == "xlsx":
                    return _xlsx_text(archive), _core_title(archive)
                return _epub_text(archive)
        if ext == "rtf":
            return _rtf_text(_decode(_bytes(path))), None
        if ext == "eml":
            return _email_text(_bytes(path))
        return _decode(_bytes(path)), None
    except ExtractError:
        raise
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError, UnicodeError) as error:
        raise ExtractError(f"unreadable document: {type(error).__name__}: {error}"[:200]) from error


def _bytes(path: str) -> bytes:
    with open(path, "rb") as file:
        data = file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ExtractError("document over 64 MB")
    return data


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_text(archive: _Archive, members: list[str], *paragraphs: str) -> str:
    """The text of XML `members` in a zip: one line per paragraph element (by local name), tabs and breaks kept."""
    lines: list[str] = []
    for member in members:
        root = ElementTree.fromstring(archive.read(member))
        for element in root.iter():
            if _local(element.tag) in paragraphs:
                parts = []
                for node in element.iter():
                    name = _local(node.tag)
                    if name in ("t", "span") or node is element:
                        if node.text:
                            parts.append(node.text)
                    elif name in ("tab", "s"):
                        parts.append(" ")
                    elif name in ("br", "line-break"):
                        parts.append("\n")
                    if node.tail and node is not element and name not in ("t",):
                        parts.append(node.tail)
                line = "".join(parts).strip()
                if line:
                    lines.append(line)
    return "\n".join(lines)


def _rows(sheets: list[tuple[str, list[list[str]]]]) -> str:
    lines, count = [], 0
    for name, rows in sheets:
        lines.append(f"Sheet: {name}")
        for row in rows:
            cells = [c.strip() for c in row if c and c.strip()]
            if cells:
                lines.append(" | ".join(cells))
                count += 1
                if count >= MAX_ROWS:
                    return "\n".join(lines)
    return "\n".join(lines)


def _xlsx_text(archive: _Archive) -> str:
    names = set(archive.namelist())
    shared = []
    if "xl/sharedStrings.xml" in names:
        for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml")):
            shared.append("".join(t.text or "" for t in item.iter() if _local(t.tag) == "t"))
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    links = {r.get("Id"): r.get("Target") for r in ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
    sheets = []
    for sheet in (e for e in workbook.iter() if _local(e.tag) == "sheet"):
        link = next((v for k, v in sheet.attrib.items() if _local(k) == "id"), None)
        target = links.get(link, "").lstrip("/")
        member = target if target.startswith("xl/") else f"xl/{target}"
        if member not in names:
            continue
        rows = []
        for row in (e for e in ElementTree.fromstring(archive.read(member)).iter() if _local(e.tag) == "row"):
            cells = []
            for cell in (c for c in row if _local(c.tag) == "c"):
                kind = cell.get("t")
                if kind == "inlineStr":
                    cells.append("".join(t.text or "" for t in cell.iter() if _local(t.tag) == "t"))
                    continue
                value = next((v.text for v in cell if _local(v.tag) == "v"), None) or ""
                cells.append(shared[int(value)] if kind == "s" and value.isdigit() and int(value) < len(shared)
                             else value)
            rows.append(cells)
            if len(rows) >= MAX_ROWS:
                break
        sheets.append((sheet.get("name") or "Sheet", rows))
    return _rows(sheets)


def _ods_text(archive: _Archive) -> str:
    root = ElementTree.fromstring(archive.read("content.xml"))
    sheets = []
    for table in (e for e in root.iter() if _local(e.tag) == "table"):
        name = next((v for k, v in table.attrib.items() if _local(k) == "name"), "Sheet")
        rows = []
        for row in (e for e in table.iter() if _local(e.tag) == "table-row"):
            rows.append([" ".join("".join(p.itertext()) for p in cell.iter() if _local(p.tag) == "p")
                         for cell in row if _local(cell.tag) == "table-cell"])
            if len(rows) >= MAX_ROWS:
                break
        sheets.append((name, rows))
    return _rows(sheets)


def _epub_text(archive: _Archive) -> tuple[str, str | None]:
    """The book's chapters in reading order (its spine), and its title."""
    container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
    opf_path = next(e.get("full-path") for e in container.iter() if _local(e.tag) == "rootfile")
    opf = ElementTree.fromstring(archive.read(opf_path))
    folder = PurePath(opf_path).parent
    title = next(((e.text or "").strip() for e in opf.iter() if _local(e.tag) == "title"), None) or None
    manifest = {e.get("id"): e.get("href") for e in opf.iter() if _local(e.tag) == "item"}
    chapters = []
    for ref in (e for e in opf.iter() if _local(e.tag) == "itemref"):
        href = manifest.get(ref.get("idref"))
        member = (folder / href).as_posix() if href else None
        if not member or member not in archive.namelist():
            continue
        raw = archive.read(member)
        chapters.append(searchtext.html_to_text(_decode(raw)))
    return "\n\n".join(chapters), title


def _core_title(archive: _Archive) -> str | None:
    try:
        root = ElementTree.fromstring(archive.read("docProps/core.xml"))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return None
    for element in root.iter():
        if _local(element.tag) == "title" and element.text and element.text.strip():
            return element.text.strip()
    return None


def _rtf_text(rtf: str) -> str:
    """Crude but enough for search: groups that hold no text dropped, control words removed, escapes decoded."""
    text = re.sub(r"\{\\\*[^{}]*\}|\{\\(fonttbl|colortbl|stylesheet|info|pict)[^{}]*(\{[^{}]*\}[^{}]*)*\}", " ", rtf)
    text = re.sub(r"\\par[d]?\b|\\line\b", "\n", text)
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1252"), text)
    text = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?|[{}]|\\", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _email_text(data: bytes) -> tuple[str, str | None]:
    message = email.message_from_bytes(data, policy=email.policy.default)
    subject = str(message.get("subject", "")).strip() or None
    header = "\n".join(f"{name}: {message[name]}" for name in ("From", "To", "Date", "Subject") if message[name])
    body = message.get_body(preferencelist=("plain", "html"))
    content = body.get_content() if body else ""
    if body is not None and body.get_content_subtype() == "html":
        content = searchtext.html_to_text(content)
    return header + "\n\n" + content, subject
