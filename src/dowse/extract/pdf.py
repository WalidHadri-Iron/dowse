"""PDFs, page by page (PDFium): a page with real text is text; a scan or a slide is a picture of the page, plus what
OCR reads on it for keyword search."""

import threading
from dataclasses import dataclass

from PIL import Image

from . import ExtractError

# PDFium isn't thread-safe: every call into it holds this.
_lock = threading.Lock()


@dataclass
class Page:
    number: int                    # from 1
    text: str                      # the text layer (may be empty for scans)
    image: Image.Image | None      # set when the page had too little text: rendered for the vision encoder


@dataclass
class Document:
    pages: list[Page]
    total: int
    title: str | None


def pages(path: str, read: range | None = None, min_text: int = 200, render_side: int = 1400) -> Document:
    """Pages in `read` (0-based; all by default)."""
    import pypdfium2 as pdfium

    with _lock:
        try:
            document = pdfium.PdfDocument(path)
        except pdfium.PdfiumError as error:
            message = str(error)
            raise ExtractError("password-protected" if "password" in message.lower()
                               else f"can't open PDF: {message}"[:200]) from error
        try:
            total = len(document)
            title = (document.get_metadata_dict().get("Title") or "").strip() or None
            wanted = read if read is not None else range(total)
            out = []
            for index in range(max(wanted.start, 0), min(wanted.stop, total)):
                page = document[index]
                textpage = page.get_textpage()
                text = textpage.get_text_bounded().replace("\r\n", "\n").strip()
                textpage.close()
                image = None
                if len(text) < min_text:
                    width, height = page.get_size()
                    scale = render_side / max(width, height, 1)
                    image = page.render(scale=scale).to_pil().convert("RGB")
                page.close()
                out.append(Page(index + 1, text, image))
            return Document(out, total, title)
        finally:
            document.close()


def page_count(path: str) -> int:
    import pypdfium2 as pdfium

    with _lock:
        document = pdfium.PdfDocument(path)
        try:
            return len(document)
        finally:
            document.close()
