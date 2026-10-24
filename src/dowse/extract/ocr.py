"""Text in pictures, by the system's own OCR: Windows.Media.Ocr (offline, in the languages installed in Windows), or
on a Mac Apple's Vision (offline, its languages detected).

For keyword search only (exact error codes, names, numbers in screenshots): a screenshot's meaning comes from
its picture.
"""

import asyncio
import io
import sys
import threading

from PIL import Image

_local = threading.local()


def available() -> bool:
    if sys.platform == "darwin":
        return _vision() is not None
    return _engine() is not None


def languages() -> list[str]:
    if sys.platform == "darwin":
        vision = _vision()
        if vision is None:
            return []
        found, _ = vision.VNRecognizeTextRequest.alloc().init().supportedRecognitionLanguagesAndReturnError_(None)
        return [str(tag) for tag in found or []]
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


def _vision():
    """Apple's Vision framework (PyObjC), or None where it isn't (not a Mac, or the package missing)."""
    if sys.platform != "darwin":
        return None
    try:
        import Vision
    except ImportError:
        return None
    return Vision


def _mac_text(vision, image: Image.Image) -> str:
    """Vision's accurate recognizer, lines read top to bottom, left to right (it returns them in no set order)."""
    from Foundation import NSData

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "PNG")
    data = buffer.getvalue()
    handler = vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(data, len(data)),
                                                                         None)
    request = vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    if hasattr(request, "setAutomaticallyDetectsLanguage_"):   # macOS 13 and later
        request.setAutomaticallyDetectsLanguage_(True)
    ok, _error = handler.performRequests_error_([request], None)
    if not ok:
        return ""
    lines = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if candidates:
            box = observation.boundingBox()   # (0, 0) is the bottom left: higher lines first
            lines.append((-round(box.origin.y + box.size.height, 2), box.origin.x, str(candidates[0].string())))
    return "\n".join(line for _, _, line in sorted(lines))


def text(image: Image.Image) -> str:
    if sys.platform == "darwin":
        vision = _vision()
        return _mac_text(vision, image) if vision is not None else ""
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
