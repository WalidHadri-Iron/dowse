"""Text in pictures, by Windows' own OCR (Windows.Media.Ocr): offline, in the languages installed in Windows.

For keyword search only (exact error codes, names, numbers in screenshots): a screenshot's meaning comes from
its picture.
"""

import asyncio
import sys
import threading

from PIL import Image

_local = threading.local()


def available() -> bool:
    return _engine() is not None


def languages() -> list[str]:
    if sys.platform != "win32":
        return []
    from winrt.windows.media.ocr import OcrEngine

    return [language.language_tag for language in OcrEngine.available_recognizer_languages]


def _engine():
    """One engine per thread (WinRT objects stay on the thread that made them), for the user's languages."""
    if sys.platform != "win32":
        return None
    if not hasattr(_local, "engine"):
        from winrt.windows.media.ocr import OcrEngine

        _local.engine = OcrEngine.try_create_from_user_profile_languages()
    return _local.engine


def text(image: Image.Image) -> str:
    engine = _engine()
    if engine is None:
        return ""
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    limit = OcrEngine.max_image_dimension
    if max(image.size) > limit:
        image = image.copy()
        image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    rgba = image.convert("RGBA")   # opaque, so the alpha mode doesn't matter
    writer = DataWriter()
    writer.write_bytes(rgba.tobytes())
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.RGBA8,
                                                    rgba.width, rgba.height)

    async def recognize():
        return await engine.recognize_async(bitmap)

    result = asyncio.run(recognize())
    return "\n".join(line.text for line in result.lines)
