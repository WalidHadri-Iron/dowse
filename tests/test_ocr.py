"""The system's own OCR reads a screenshot's words (keyword search only): Vision on a Mac, Windows' OCR on Windows."""

import pytest
from PIL import Image, ImageDraw, ImageFont

from dowse.extract import ocr


def test_the_systems_ocr_reads_the_words_of_a_screenshot():
    if not ocr.available():
        pytest.skip("no OCR engine on this system")
    image = Image.new("RGB", (1200, 360), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=56)
    draw.text((40, 60), "Guest WiFi password", fill="black", font=font)
    draw.text((40, 200), "olive-tree-42", fill="black", font=font)
    words = ocr.text(image)
    assert "WiFi" in words and "password" in words and "olive" in words, words
    assert words.index("password") < words.index("olive"), "lines read top to bottom"
