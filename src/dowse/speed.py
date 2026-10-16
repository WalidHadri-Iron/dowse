"""How fast this PC reads files: measured on it, once the model is here, not guessed from another machine.

`measure` times what indexing does most, on the runtime it will use and with the same settings (`Options`): pictures
at each budget (a photo, a screenshot, a PDF page, a video frame), text passages, 30 s of sound, and OCR. A typical
file of each kind is then a sum of those (`file_seconds`): a PDF is so many pages, a video so many frames and windows
of sound. Those proportions don't depend on the machine; the measured times do.

Before a measurement there's a guess (`GUESS`): a GPU and a CPU of this project's test PC, said to be rough.
"""

import io
import json
import math
import time
import wave
from dataclasses import asdict, dataclass, replace

import numpy as np
from PIL import Image, ImageDraw

from . import modelfiles, paths, runtime
from .embedder import Embedder, Prompts
from .reading import Options
from .rules import Kind

VIDEO_SECONDS, AUDIO_SECONDS = 120.0, 180.0   # a typical video and recording
PDF_PAGES, PDF_SCANNED, DOC_PASSAGES = 8, 2, 4  # a typical PDF (pages of text, pages read as pictures), document


@dataclass(frozen=True)
class Speed:
    """Seconds each, on one runtime (`device`: its description)."""
    photo: float
    screenshot: float
    page: float
    frame: float
    passage: float
    sound: float          # 30 s of audio
    ocr: float            # one screenshot's words (on the CPU, whatever the runtime)
    device: str = ""
    measured: bool = True


# This project's test PC (an RTX 4070 Ti with Vulkan; a 12-thread CPU), measured: only until this PC is.
# Composed into files there, they're within ~1.5× of whole files timed while indexing.
GUESS = {
    True: Speed(photo=0.094, screenshot=0.14, page=0.089, frame=0.026, passage=0.016, sound=0.10, ocr=0.042,
                measured=False),
    False: Speed(photo=0.37, screenshot=0.72, page=0.72, frame=0.37, passage=0.17, sound=1.26, ocr=0.049,
                 measured=False),
}


def file_seconds(speed: Speed, kind: Kind, options: Options) -> float:
    """About how long a typical file of `kind` takes to read."""
    if kind is Kind.IMAGE:
        return speed.photo + 0.02                               # + decoding and sizing
    if kind is Kind.SCREENSHOT:
        return speed.screenshot + speed.ocr + 0.02
    if kind is Kind.PDF:
        return (PDF_PAGES - PDF_SCANNED) * speed.passage + PDF_SCANNED * (speed.page + speed.ocr) + 0.05
    if kind is Kind.DOC:
        return DOC_PASSAGES * speed.passage + 0.01
    windows = math.ceil(max(AUDIO_SECONDS - options.audio_window, 0) / options.audio_hop) + 1
    if kind is Kind.AUDIO:
        return windows * speed.sound + 0.2                      # + decoding the sound
    frames = min(VIDEO_SECONDS / options.frame_interval, options.max_frames)
    windows = math.ceil(max(VIDEO_SECONDS - options.audio_window, 0) / options.audio_hop) + 1
    return frames * speed.frame + windows * speed.sound + 1.0   # + decoding (seeking keyframes)


def seconds(counts: dict[Kind, int], speed: Speed, options: Options) -> float:
    return sum(file_seconds(speed, kind, options) * n for kind, n in counts.items())


# Remembered per runtime: a switch from the graphics card to the processor measures again.

def _file():
    return paths.home() / "speed.json"


def load(choice: runtime.Choice | None, gpu: bool, model: str | modelfiles.Model | None = None) -> Speed:
    """What was measured on `choice`, else the guess for a GPU or a CPU."""
    profile = modelfiles.get(model)
    if choice is not None:
        try:
            key = choice.describe() if profile.id == modelfiles.DEFAULT else f"{choice.describe()} · {profile.id}"
            found = json.loads(_file().read_text(encoding="utf-8")).get(key)
            if found:
                return Speed(**found)
        except (OSError, ValueError, TypeError):
            pass
    return GUESS[gpu] if profile.media else replace(GUESS[gpu], photo=0, screenshot=0, page=0, frame=0, sound=0)


def save(speed: Speed) -> None:
    try:
        known = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    known[speed.device] = asdict(speed)
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(known, indent=2), encoding="utf-8")


# Measuring

def _picture(seed: int) -> Image.Image:
    """A photo-like picture, about the size it reaches the model at (sizing a big photo happens on the reading
    threads, alongside the model: it isn't what's timed here). The model's cost depends on the budget, not the
    content."""
    noise = np.random.default_rng(seed).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    return Image.fromarray(noise)


def _screenshot() -> Image.Image:
    image = Image.new("RGB", (1280, 720), "white")
    draw = ImageDraw.Draw(image)
    for line in range(24):
        draw.text((40, 30 + line * 34), f"Invoice 2026-{line:04d} payment due Friday, error E-{4000 + line}", "black")
    return image


def _sound() -> bytes:
    """30 s of a gliding tone, as 16 kHz WAV."""
    rate = 16000
    t = np.arange(30 * rate) / rate
    samples = (0.3 * np.sin(2 * np.pi * (220 + 40 * np.sin(t)) * t) * 32767).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(samples.tobytes())
    return out.getvalue()


def measure(choice: runtime.Choice, options: Options | None = None, model: str | modelfiles.Model | None = None) -> Speed:
    """Times this PC's runtime and remembers the result: ~10 s on a GPU, ~20 s on a CPU, starting the server
    included."""
    from .extract import ocr

    options = options or Options.for_device(choice.device is not None)
    profile = modelfiles.get(model)
    pictures = [_picture(i) for i in range(4)]
    shot = _screenshot()
    passage = Prompts.document("The committee reviewed the budget proposal and agreed on the timeline. " * 25)
    sound = _sound()
    try:
        ocr.text(shot)   # its first call starts Windows' OCR engine: not part of a file's time
        read_words = True
    except Exception:
        read_words = False
    with runtime.Server(choice, model=profile) as server, Embedder(server.url, parallel=server.slots, model=profile) as embedder:
        embedder.texts(["warm up"])
        embedder.images(pictures[:1], options.frame_budget)
        embedder.audio([sound])

        def each(work, count: int) -> float:
            started = time.perf_counter()
            work()
            return (time.perf_counter() - started) / count

        n = 2 * server.slots   # what indexing keeps in flight
        speed = Speed(
            photo=each(lambda: embedder.images((pictures * n)[:n], options.photo_budget), n) if profile.media else 0,
            screenshot=each(lambda: embedder.images([shot] * n, options.screenshot_budget), n) if profile.media else 0,
            page=each(lambda: embedder.images([shot] * n, options.page_budget), n) if profile.media else 0,
            frame=each(lambda: embedder.images((pictures * n)[:n], options.frame_budget), n) if profile.media else 0,
            passage=each(lambda: embedder.texts([passage] * (4 * n)), 4 * n),
            sound=each(lambda: embedder.audio([sound] * n), n) if profile.media else 0,
            ocr=each(lambda: ocr.text(shot), 1) if read_words else GUESS[True].ocr,
            device=choice.describe() if profile.id == modelfiles.DEFAULT else f"{choice.describe()} · {profile.id}",
        )
    save(speed)
    return speed
