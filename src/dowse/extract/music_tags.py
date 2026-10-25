"""Bounded, read-only music metadata, independent of sound decoding or the chosen embedding model.

Use the same parser for ID3 and FLAC (and the other supported audio containers). Seek past cover art rather than
rejecting a perfectly good artist/title because the artwork makes its tag block large. Never read cloud placeholders.
"""

from tinytag import TinyTag, TinyTagException

from ..rules import attributes, cloud_only

MAX_READ_BYTES = 1 << 20
MAX_OPERATIONS = 8192
MAX_VALUE_CHARS = 256
FIELDS = {"title": "title", "artist": "artist", "album": "album", "albumartist": "album_artist",
          "track": "track", "year": "date", "genre": "genre"}


class _Bounded:
    """The parser may seek over large pictures, but cannot request/accumulate unbounded metadata or loop forever."""
    def __init__(self, source):
        self.source = source
        self.remaining = MAX_READ_BYTES
        self.operations = MAX_OPERATIONS

    def _operation(self):
        self.operations -= 1
        if self.operations < 0:
            raise ValueError("music metadata operation budget exceeded")

    def read(self, size=-1):
        self._operation()
        if size < 0 or size > self.remaining:
            raise ValueError("music metadata read budget exceeded")
        data = self.source.read(size)
        self.remaining -= len(data)
        if data and len(data) < size and any(data):
            raise ValueError("truncated music metadata")
        return data

    def seek(self, offset, whence=0):
        self._operation()
        return self.source.seek(offset, whence)

    def tell(self):
        return self.source.tell()


def read(path: str) -> dict[str, str]:
    """Short, normalized fields only; missing/broken/over-budget tags never prevent the sound being indexed."""
    if cloud_only(attributes(path)):
        return {}
    try:
        with open(path, "rb") as source:
            tag = TinyTag.get(filename=path, file_obj=_Bounded(source), duration=False, image=False,
                              check_magic_bytes=False)
        values = {}
        for field, name in FIELDS.items():
            entries = [getattr(tag, field), *tag.other.get(field, [])]
            text = "; ".join(dict.fromkeys(clean for value in entries if value is not None
                                         if (clean := " ".join(str(value).replace("\0", " ").split()))))
            if field == "track" and text and tag.track_total:
                text += f"/{tag.track_total}"
            if text:
                values[name] = text[:MAX_VALUE_CHARS]
        return values
    except (OSError, ValueError, TinyTagException):
        return {}
