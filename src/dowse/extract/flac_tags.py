"""Read FLAC Vorbis comments without decoding sound or loading embedded pictures.

FLAC starts with ``fLaC``, followed by metadata blocks. A block has a four-byte
header: the high bit ends the list, seven bits give its type, and the remaining
three bytes give its length. Type 4 contains Vorbis comments; type 6 is usually
cover art. Seek over everything except type 4, so even a large cover image is
never read into memory. Refuse comments larger than 1 MiB and ignore damaged
metadata rather than preventing the audio reader from indexing the sound.
"""

import os
import struct

MAX_COMMENTS = 1024 * 1024
MAX_VALUE_CHARS = 256
FIELDS = {"TITLE": "title", "ARTIST": "artist", "ALBUM": "album", "ALBUMARTIST": "album_artist",
          "TRACKNUMBER": "track", "DATE": "date", "YEAR": "year", "GENRE": "genre"}


def _comments(data: bytes) -> dict[str, str]:
    """Decode the length-prefixed UTF-8 Vorbis fields, checking every boundary.

    The vendor and entry counts are little-endian; unknown fields, pictures and
    vendor strings are not indexed. Multiple values of a known key are joined.
    """
    position = 0

    def take(length: int) -> bytes:
        nonlocal position
        if length > len(data) - position:
            raise ValueError("truncated FLAC comments")
        result = data[position:position + length]
        position += length
        return result

    def number() -> int:
        return struct.unpack("<I", take(4))[0]

    take(number())  # encoder's vendor string, not music metadata
    count = number()
    if count > len(data) // 5:  # each KEY=VALUE needs at least a length and one byte
        raise ValueError("invalid FLAC comment count")
    values: dict[str, str] = {}
    for _ in range(count):
        entry = take(number()).decode("utf-8")
        key, separator, value = entry.partition("=")
        field = FIELDS.get(key.upper()) if separator else None
        if field:
            clean = " ".join(value.split())[:MAX_VALUE_CHARS]
            if clean:
                values[field] = (values[field] + "; " + clean)[:MAX_VALUE_CHARS] if field in values else clean
    return values


def read(path: str) -> dict[str, str]:
    """Return selected FLAC comments, or {} for absent/invalid/oversized metadata.

    Headers are inspected only within the file; metadata blocks are skipped by
    seeking, not decoded. Only one bounded Vorbis-comment block is read. All
    values returned are short plain strings suitable for JSON and keyword search.
    """
    try:
        with open(path, "rb") as source:
            if source.read(4) != b"fLaC":
                return {}
            size = os.fstat(source.fileno()).st_size
            while source.tell() + 4 <= size:
                header = source.read(4)
                kind = header[0] & 0x7f
                length = int.from_bytes(header[1:4], "big")
                if source.tell() + length > size:
                    return {}
                if kind == 4:
                    if length > MAX_COMMENTS:
                        return {}
                    return _comments(source.read(length))
                source.seek(length, os.SEEK_CUR)
                if header[0] & 0x80:
                    break
    except (OSError, UnicodeError, ValueError, struct.error):
        pass
    return {}