"""Small, bounded ID3v2 text reader for audio files.

Only a tag at the start of the file is read; sound decoding remains PyAV's job.
Artwork, lyrics, comments and arbitrary/private frames are deliberately ignored.
Missing or broken tags never prevent the recording itself from being indexed.
"""

import io

from mutagen import MutagenError
from mutagen.id3 import ID3

# Cap both the on-disk read and what we keep in the index. ID3 tags can contain
# megabytes of embedded artwork, which should not be loaded just to find a title.
MAX_TAG_BYTES = 1024 * 1024
MAX_VALUE_CHARS = 256
FRAMES = {"TIT2": "title", "TPE1": "artist", "TALB": "album", "TPE2": "album_artist",
          "TRCK": "track", "TDRC": "date", "TYER": "year", "TCON": "genre"}


def read(path: str) -> dict[str, str]:
    """Return selected ID3v2 fields, or {} for no tag, bad tag or an oversized tag.

    ID3v2's ten-byte header holds a four-byte *synchsafe* payload size. Check it
    before handing a bounded in-memory copy to Mutagen: a large APIC cover art
    must not cause the indexer to read the entire tag into memory. The returned
    strings are plain, length-limited JSON/FTS-safe values, not Mutagen frames.
    """
    try:
        with open(path, "rb") as source:
            header = source.read(10)
            if len(header) != 10 or header[:3] != b"ID3" or any(b & 0x80 for b in header[6:10]):
                return {}
            size = 10 + sum(b << shift for b, shift in zip(header[6:10], (21, 14, 7, 0)))
            if header[5] & 0x10:  # ID3v2.4 footer
                size += 10
            if size > MAX_TAG_BYTES:
                return {}
            tag = ID3(fileobj=io.BytesIO(header + source.read(size - 10)))
    except (OSError, MutagenError, ValueError):
        return {}

    values = {}
    for frame, name in FRAMES.items():
        if frame in tag:
            text = "; ".join(str(item) for item in tag[frame].text)
            text = " ".join(text.split())[:MAX_VALUE_CHARS]
            if text:
                values[name] = text
    return values