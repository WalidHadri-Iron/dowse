"""Makes the inputs of the reference-vector test (tests/reference): a landscape, an app screen and a voice, all drawn
or spoken here, so the test owns every byte it checks. Run once; the files are committed. Then write their vectors:

    uv run python scripts/make_reference_inputs.py
    DOWSE_WRITE_REFERENCE=1 uv run pytest tests/test_reference.py
"""

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parents[1] / "tests" / "reference"
SPEECH = ("The train to the coast leaves at seven in the morning. "
          "Remember to bring a raincoat, the tickets, and something to read.")


def font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def landscape(target: Path) -> None:
    """A lake between mountains under a low sun, with a pine on the shore."""
    width, height = 1024, 768
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):   # sky, light toward the horizon
        t = y / height
        draw.line([(0, y), (width, y)], fill=(int(70 + 150 * t), int(120 + 90 * t), int(200 + 40 * t)))
    draw.ellipse((700, 160, 820, 280), fill=(255, 220, 150))
    draw.polygon([(0, 470), (180, 230), (330, 420), (470, 200), (650, 450), (820, 280), (1024, 470)],
                 fill=(90, 100, 120))
    draw.polygon([(180, 230), (230, 290), (140, 290)], fill=(240, 240, 245))
    draw.polygon([(470, 200), (530, 270), (420, 270)], fill=(240, 240, 245))
    draw.rectangle((0, 470, width, height), fill=(40, 90, 130))
    for y in range(480, height, 14):   # ripples
        draw.line([(0, y), (width, y + 4)], fill=(60, 115, 155), width=2)
    draw.rectangle((0, 640, width, height), fill=(70, 110, 50))
    draw.rectangle((140, 520, 158, 660), fill=(80, 55, 30))
    for i in range(6):
        draw.polygon([(149, 420 + i * 30), (95 + i * 6, 520 + i * 25), (203 - i * 6, 520 + i * 25)], fill=(30, 80, 40))
    image.filter(ImageFilter.GaussianBlur(1.2)).save(target, quality=92)


def screen(target: Path) -> None:
    """An app window with a sidebar and an error dialog: the kind of screenshot people search by its words."""
    image = Image.new("RGB", (1440, 900), (243, 243, 243))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1440, 40), fill=(32, 32, 32))
    draw.text((16, 8), "Settings", fill="white", font=font(18))
    draw.rectangle((0, 40, 280, 900), fill=(230, 230, 230))
    for i, item in enumerate(["System", "Bluetooth & devices", "Network", "Personalization", "Apps", "Accounts",
                              "Windows Update"]):
        draw.text((32, 80 + i * 48), item, fill=(30, 30, 30), font=font(20))
    draw.rounded_rectangle((470, 280, 1170, 600), 12, fill="white", outline=(200, 200, 200), width=2)
    draw.text((510, 310), "Update failed", fill=(180, 30, 30), font=font(34))
    draw.text((510, 380), "There were problems installing some updates.", fill=(30, 30, 30), font=font(22))
    draw.text((510, 420), "Error 0x80070005: Access is denied.", fill=(30, 30, 30), font=font(22))
    draw.rounded_rectangle((950, 520, 1130, 570), 6, fill=(0, 95, 184))
    draw.text((1010, 530), "Retry", fill="white", font=font(22))
    image.save(target)


def speech(target: Path) -> None:
    """Windows' own speech synthesizer, straight to 16 kHz mono (what the model hears)."""
    if sys.platform != "win32":
        raise SystemExit("the voice is made with Windows' speech synthesizer")
    with tempfile.TemporaryDirectory() as folder:
        raw = Path(folder) / "speech.wav"
        script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, "
                  "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
                  "[System.Speech.AudioFormat.AudioChannel]::Mono); "
                  f"$s.SetOutputToWaveFile('{raw}', $f); $s.Speak([Console]::In.ReadToEnd()); $s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-Command", script], input=SPEECH, text=True, check=True)
        with wave.open(str(raw)) as clip:
            assert (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) == (16000, 1, 2)
        target.write_bytes(raw.read_bytes())


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    landscape(OUT / "landscape.jpg")
    screen(OUT / "screen.png")
    speech(OUT / "speech.wav")
    print(f"wrote {OUT}")
