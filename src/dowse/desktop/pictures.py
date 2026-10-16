"""Pictures of files for the interface: the part that matched, not a file-type icon. A photo itself, the PDF page the
hit is on (with the query's words marked, cropped to them), the video frame at its moment. Rendered on the request's
thread, encoded as WebP, and kept in a small memory cache keyed by the file's identity and the request, and on disk
beside the index (its encryption covers them): a picture made once is there after a restart. On the heavy library
(10,700 files), the wall's first 60 pictures took 2.7 s to make (a video's frame 0.3 s, up to 1.7 s), 0.1 s once made.
"""

import hashlib
import io
import os
import threading
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageDraw

from .. import paths
from ..extract import images, media, pdf
from ..rules import Kind, attributes, cloud_only

VISUAL = (Kind.IMAGE, Kind.SCREENSHOT, Kind.PDF, Kind.VIDEO)
HIGHLIGHTER = (255, 214, 10, 110)   # a real highlighter's yellow: readable on any page, in any theme
ASPECT = 1.62                         # width / height of a marked PDF crop


def render(path: str, kind: Kind, loc: float | None, pixels: int, terms: tuple[str, ...] = (),
           whole: bool = False) -> Image.Image | None:
    """The matching part of the file, `pixels` on its long side at most; None when there's nothing to show. For a
    PDF page with `terms`, the page at reading size around the first of them, each marked as with a highlighter
    (`whole`: the whole page, marked, for looking at closely)."""
    if kind in (Kind.IMAGE, Kind.SCREENSHOT):
        return images.load(path, 0, max_side=pixels).image
    if kind is Kind.PDF:
        return _pdf_page(path, loc, pixels, terms, whole)
    if kind is Kind.VIDEO:
        with media.FrameReader(path, max_side=pixels) as reader:
            duration = reader.duration or 0
            starts = [float(loc)] if loc is not None else \
                [min(duration * share, duration - 1) for share in (0.1, 0.3, 0.5, 0.7)]
            for start in [max(s, 0.0) for s in starts] + [0.0]:
                # One frame a place (a dark one: the next place). Six half a second apart each meant decoding from a
                # keyframe six times, seconds each for 4K VP9 (whose keyframes are far apart): 20 s while reading.
                frames = reader.frames(start, min(start + 3, duration or start + 3), spacing=3)
                for frame in frames:
                    if _lit(frame.image):
                        return frame.image
                if loc is not None and frames:
                    return frames[0].image   # the moment that matched, dark or not
        return None   # black throughout (a recording of nothing): the interface shows the kind, not a black square
    return None


def _lit(picture: Image.Image) -> bool:
    """Not a black (or nearly) frame: a fade, a title card's background, a recording before its window drew."""
    from PIL import ImageStat

    return ImageStat.Stat(picture.convert("L").resize((32, 18))).mean[0] > 16


def _pdf_page(path: str, loc: float | None, pixels: int, terms: tuple[str, ...], whole: bool = False) -> Image.Image:
    import pypdfium2 as pdfium

    with pdf._lock:   # PDFium takes one caller at a time
        document = pdfium.PdfDocument(path)
        try:
            page = document[max(int(loc or 1), 1) - 1]
            width, height = page.get_size()
            # Marked: the page as wide as `pixels` (reading size), so the words can be read once cropped.
            scale = pixels / max(width, 1) if terms else pixels / max(width, height, 1)
            picture = page.render(scale=scale).to_pil().convert("RGB")
            boxes, column = [], None
            if terms:
                text = page.get_textpage()
                lines = [text.get_rect(i) for i in range(text.count_rects())]   # where the page's text sits
                if lines:
                    column = (min(r[0] for r in lines) * scale, max(r[2] for r in lines) * scale)
                for term in terms:
                    search = text.search(term, match_case=False, match_whole_word=False)
                    while len(boxes) < 60 and (found := search.get_next()):
                        start, count = found
                        for i in range(text.count_rects(start, count)):
                            left, bottom, right, top = text.get_rect(i)
                            boxes.append((left * scale, (height - top) * scale, right * scale,
                                          (height - bottom) * scale))
                    search.close()
                text.close()
            page.close()
        finally:
            document.close()
    if not boxes:
        return picture
    layer = Image.new("RGBA", picture.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for left, top, right, bottom in boxes:
        draw.rounded_rectangle((left - 2, top - 1, right + 2, bottom + 1), radius=3, fill=HIGHLIGHTER)
    picture = Image.alpha_composite(picture.convert("RGBA"), layer).convert("RGB")
    if whole:
        return picture
    # Crop to the text column (no page margins: the words get bigger), at the preview's shape, around the first mark.
    margin = picture.width * 0.03
    left, right = column if column else (0, picture.width)
    left, right = max(left - margin, 0), min(right + margin, picture.width)
    window = min(round((right - left) / ASPECT), picture.height)
    first = min(top for _, top, _, _ in boxes)
    top = int(min(max(first - window * 0.25, 0), picture.height - window))
    return picture.crop((round(left), top, round(right), top + window))


class Pictures:
    """Encoded pictures, rendered on demand and remembered: in memory (least recently used goes first) and on disk
    (`folder`, up to `disk_bytes`: the oldest go when it's full)."""

    NOTHING = b""   # on disk: the file has nothing to show (a black recording), so it isn't tried again

    def __init__(self, capacity_bytes: int = 64 << 20, folder: Path | None = None, disk_bytes: int = 1 << 30):
        self.capacity = capacity_bytes
        self._cache: OrderedDict[tuple, bytes | None] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()
        self.folder = folder if folder is not None else paths.index() / "Pictures"
        self.disk_bytes = disk_bytes
        self._written = 0   # bytes written since the folder was last trimmed

    def _file(self, key: tuple) -> Path:
        name = hashlib.sha1(repr(key).encode("utf-8", "surrogatepass")).hexdigest()
        return self.folder / name[:2] / f"{name}.webp"

    def _read(self, key: tuple) -> bytes | None | bool:
        """The picture kept on disk, None if it has nothing to show, False if it isn't kept."""
        try:
            data = self._file(key).read_bytes()
        except OSError:
            return False
        return data if data != self.NOTHING else None

    def _keep(self, key: tuple, data: bytes | None) -> None:
        target = self._file(key)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(f".{threading.get_ident()}.tmp")
            temporary.write_bytes(data or self.NOTHING)
            temporary.replace(target)
        except OSError:
            return   # a full disk, a folder gone: pictures are made again next time
        with self._lock:
            self._written += len(data or b"") + 4096
            trim = self._written > self.disk_bytes // 8
            if trim:
                self._written = 0
        if trim:
            threading.Thread(target=self.trim, daemon=True, name="pictures-trim").start()

    def trim(self) -> None:
        """The oldest pictures out until the folder is within three quarters of `disk_bytes` (so not every picture
        written trims)."""
        try:
            kept = [(entry.stat().st_mtime, entry.stat().st_size, entry.path)
                    for sub in os.scandir(self.folder) if sub.is_dir() for entry in os.scandir(sub.path)]
        except OSError:
            return
        total = sum(size for _, size, _ in kept)
        for _, size, path in sorted(kept):
            if total <= self.disk_bytes * 3 // 4:
                break
            try:
                os.remove(path)
                total -= size
            except OSError:
                pass

    def get(self, path: str, kind: Kind, mtime: float, loc: float | None, pixels: int,
            terms: tuple[str, ...] = (), whole: bool = False) -> bytes | None:
        """The WebP bytes, or None when the file has nothing to show (or can't be read now)."""
        terms = terms if kind is Kind.PDF else ()
        key = (path, mtime, loc if kind in (Kind.PDF, Kind.VIDEO) else None, pixels, terms, whole)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        # Explore warms thumbnails directly, not through the HTTP route's online-only gate.
        # Never hydrate a cloud placeholder (a potentially unbounded wait/download) for a preview.
        if cloud_only(attributes(path)):
            return None   # (not remembered: once the file is on this PC, it has a picture)
        data = self._read(key)
        if data is False:
            failed = False
            try:
                picture = render(path, kind, loc, pixels, terms, whole)
            except Exception:
                picture, failed = None, True   # moved, locked, damaged: the interface shows the kind instead
            data = None
            if picture is not None:
                out = io.BytesIO()
                picture.convert("RGB").save(out, "WEBP", quality=82, method=4)
                data = out.getvalue()
            if not failed:   # (a file that couldn't be read now may be readable later: not remembered as nothing)
                self._keep(key, data)
        with self._lock:
            self._cache[key] = data
            self._size += len(data or b"")
            while self._size > self.capacity and self._cache:
                _, dropped = self._cache.popitem(last=False)
                self._size -= len(dropped or b"")
        return data
