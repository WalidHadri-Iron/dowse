"""Pictures: decoded straight to about the size the model needs (a 48 MP photo is never decoded whole)."""

from dataclasses import dataclass

from PIL import ExifTags, Image, ImageOps

from . import ExtractError

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:   # HEIC stays unreadable
    pass

Image.MAX_IMAGE_PIXELS = 200_000_000   # big panoramas are fine; decompression bombs are not

# 1120 tokens fill about 1,000 × 1,000 px: nothing the model sees needs more.
MAX_SIDE = 1600


@dataclass
class Picture:
    image: Image.Image   # RGB, upright, at most MAX_SIDE (or `full_side`) on its long side
    width: int           # of the file
    height: int
    taken: str | None    # EXIF DateTimeOriginal


def load(path: str, min_side: int = 256, max_side: int = MAX_SIDE) -> Picture:
    try:
        with Image.open(path) as source:
            width, height = source.size
            if max(width, height) < min_side:
                raise ExtractError(f"small image {width}×{height}")
            taken = _taken(source)
            source.draft("RGB", (max_side, max_side))   # JPEG: decode at 1/2, 1/4 or 1/8 scale when that's enough
            image = ImageOps.exif_transpose(source.convert("RGB"))
    except ExtractError:
        raise
    except Exception as error:   # PIL raises many kinds for broken files
        raise ExtractError(f"unreadable image: {type(error).__name__}: {error}"[:200]) from error
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return Picture(image, width, height, taken)


def _taken(image: Image.Image) -> str | None:
    try:
        exif = image.getexif().get_ifd(ExifTags.IFD.Exif)
        value = exif.get(ExifTags.Base.DateTimeOriginal)
        return str(value) if value else None
    except Exception:
        return None
