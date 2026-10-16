"""Audio and video (FFmpeg, through PyAV), read as streams that can start anywhere: a long recording is read a step at
a time, and a run that stopped goes on from where it was.

The model hears at most 30 s of one input and silently ignores the rest, so sound is cut into overlapping windows
(30 s every 25 s) and each match points to its moment. Video is a frame every few seconds, one
per shot, plus its soundtrack the same way.
"""

import io
import os
import wave
from collections.abc import Iterator
from dataclasses import dataclass

import av
import numpy as np
from PIL import Image

from .. import foreground
from . import ExtractError

RATE = 16_000
SILENCE = 0.003   # RMS ≈ −50 dBFS: silence isn't worth a vector


@dataclass
class Window:
    """A stretch of sound as a 16 kHz mono WAV, in memory (30 s is under 1 MB)."""
    start: float
    end: float
    wav: bytes


@dataclass
class Frame:
    image: Image.Image
    time: float


def open_media(path: str) -> av.container.InputContainer:
    try:
        return av.open(path)
    except (av.error.FFmpegError, UnicodeDecodeError) as error:
        raise ExtractError(f"unreadable media: {error}"[:200]) from error


def duration_of(container) -> float:
    """Seconds, from the container or its longest stream; 0 when neither says."""
    if container.duration:
        return container.duration / av.time_base
    lengths = [float(s.duration * s.time_base) for s in container.streams if s.duration and s.time_base]
    return max(lengths, default=0.0)


def wav_bytes(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(samples.astype("<i2").tobytes())
    return buffer.getvalue()


def _silent(samples: np.ndarray) -> bool:
    return float(np.sqrt(np.mean((samples.astype(np.float32) / 32768) ** 2))) <= SILENCE


def sound(path: str, start: float = 0.0, window: float = 30.0, hop: float = 25.0,
          minimum: float = 2.0) -> Iterator[Window]:
    """The first audio stream from `start` seconds on, as `window`-second pieces `hop` seconds apart (`start` should
    be a multiple of `hop`, so a resumed read lands on the same windows as an uninterrupted one). Silent pieces are
    skipped. Streams: memory holds one window, even for a 3-hour podcast."""
    window_n, hop_n = int(window * RATE), int(hop * RATE)
    first = round(start * RATE)   # the sample the first window starts at
    with open_media(path) as container:
        if not container.streams.audio:
            return
        stream = container.streams.audio[0]
        stream.thread_type = "AUTO"
        if start > 0:
            container.seek(int(start / stream.time_base), stream=stream, backward=True)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        buffer = np.zeros(0, np.int16)
        offset: int | None = None   # the sample buffer[0] is, in the file
        chunks: list[np.ndarray] = []
        waiting = 0

        def take(frame) -> None:
            nonlocal offset, waiting
            for resampled in resampler.resample(frame):
                samples = resampled.to_ndarray().reshape(-1)
                if offset is None:
                    # Windows sit on a fixed grid (k × hop), wherever decoding starts: a seek lands before `start`
                    # (cut the difference), and a codec's start delay (mp3's 25 ms) begins after it (pad silence).
                    landed = round((resampled.time if resampled.time is not None else 0.0) * RATE)
                    if landed >= first:
                        samples = np.concatenate([np.zeros(landed - first, np.int16), samples])
                    else:
                        samples = samples[first - landed:]
                    offset = first
                chunks.append(samples)
                waiting += samples.size

        try:
            for frame in container.decode(stream):
                take(frame)
                if offset is None or len(buffer) + waiting < window_n:
                    continue
                buffer, waiting = np.concatenate([buffer, *chunks]), 0
                chunks.clear()
                while len(buffer) >= window_n:
                    foreground.wait()   # the person first: a window at a time
                    if not _silent(buffer[:window_n]):
                        yield Window(offset / RATE, (offset + window_n) / RATE, wav_bytes(buffer[:window_n]))
                    buffer = buffer[hop_n:]
                    offset += hop_n
            take(None)   # flush the resampler
        except av.error.FFmpegError as error:
            if offset is None:
                raise ExtractError(f"audio decoding failed: {error}"[:200]) from error
            # A damaged tail: what was read stands.
        if offset is None:
            return
        buffer = np.concatenate([buffer, *chunks])
        # The tail: sound past the previous window's overlap, or all of it when it's shorter than a window.
        covered = 0 if offset == first else window_n - hop_n
        if len(buffer) - covered >= minimum * RATE and not _silent(buffer):
            yield Window(offset / RATE, (offset + len(buffer)) / RATE, wav_bytes(buffer))


def average_hash(image: Image.Image) -> int:
    """8×8 average hash: frames of one shot differ in a few bits at most."""
    pixels = np.asarray(image.convert("L").resize((8, 8), Image.Resampling.BILINEAR), np.float32).reshape(-1)
    return int(sum(1 << i for i, above in enumerate(pixels > pixels.mean()) if above))


def same_shot(a: int | None, b: int) -> bool:
    return a is not None and bin(a ^ b).count("1") <= 4


class FrameReader:
    """A video's frames, a stretch of time at a time. Decodes only keyframes (cheap: one every few seconds of video),
    then fills gaps longer than `gap` (a screen recording can go a minute between keyframes) by seeking into them."""

    def __init__(self, path: str, max_side: int = 768, gap: float = 15.0):
        self.container = open_media(path)
        self.duration = duration_of(self.container)
        self.has_audio = bool(self.container.streams.audio)
        video = [s for s in self.container.streams.video
                 if not s.disposition & av.stream.Disposition.attached_pic]   # cover art isn't a video
        self.stream = video[0] if video else None
        self.size = (self.stream.codec_context.width, self.stream.codec_context.height) if self.stream else (0, 0)
        if self.stream:
            self.stream.thread_type = "AUTO"
            # Each decoding thread holds frames in flight: for 4K, all 24 of a 24-thread CPU took 1.1 GB for a video
            # (60 s to read 415 s of 2160p60 VP9); 8 took 0.77 GB (64 s), 4 took 0.55 GB (84 s).
            self.stream.codec_context.thread_count = min(8, os.cpu_count() or 8)
        self.max_side, self.gap = max_side, gap

    def close(self) -> None:
        self.container.close()

    def __enter__(self) -> "FrameReader":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def _image(self, frame) -> Image.Image:
        image = frame.to_image()
        rotation = getattr(frame, "rotation", 0) or 0
        if rotation % 360:
            image = image.rotate(rotation, expand=True)   # phones store portrait video sideways, with a rotation
        image.thumbnail((self.max_side, self.max_side), Image.Resampling.BILINEAR)
        return image

    def frames(self, start: float, end: float, spacing: float) -> list[Frame]:
        """Frames in [start, end), at least `spacing` seconds apart, in order. Reading a video in stretches gets
        the frames reading it whole would: gaps are measured between the keyframes around the stretch, not from
        its edges."""
        if self.stream is None or end <= start:
            return []
        stream = self.stream
        # The keyframes' times from the one before `start` to the one after (gaps are measured between them), and the
        # frames kept: the first keyframe from `start`, then each first one `spacing` after the last kept. Only those
        # are made pictures. A video whose every frame is a keyframe (intra-only camera footage, some screen
        # recordings) once held a picture of each until the stretch was read: 25,000 for 415 s of 4K at 60 fps, the
        # app's memory past 15 GB. There, after each frame kept, it seeks to the next one wanted instead of decoding
        # the ones between (the same frames: seeking lands on the keyframe at or before that time).
        times: list[float] = []
        picked: list[Frame] = []
        beyond, dense, position = False, False, start
        stream.codec_context.skip_frame = "NONKEY"
        try:
            while True:
                self.container.seek(int(position / stream.time_base), stream=stream, backward=True)
                ahead = None
                for frame in self.container.decode(stream):
                    if frame.time is None or (times and frame.time <= times[-1]):
                        continue
                    if times and frame.time - times[-1] < spacing / 2:
                        dense = True
                    times.append(frame.time)
                    if frame.time >= end:
                        beyond = True
                        break
                    if frame.time >= start and (not picked or frame.time >= picked[-1].time + spacing):
                        foreground.wait()   # the person first: a frame at a time
                        picked.append(Frame(self._image(frame), frame.time))
                        if dense and spacing <= self.gap / 2 and frame.time + spacing < end:
                            ahead = frame.time + spacing
                            break
                if ahead is None:
                    break
                position = ahead
        except av.error.FFmpegError:
            pass   # a damaged stretch: keep what was read
        stream.codec_context.skip_frame = "DEFAULT"
        # Gaps: one seek each, then decode forward through it, taking a frame as each moment wanted goes by.
        if not beyond:   # the file ended inside the stretch
            times.append(max(self.duration, end))
        for before, after in zip(times, times[1:]):
            if after - before <= self.gap:
                continue
            targets = [t for t in np.arange(before + spacing, after - spacing / 2, spacing) if start <= t < end]
            if not targets:
                continue
            try:
                self.container.seek(int(targets[0] / stream.time_base), stream=stream, backward=True)
                for frame in self.container.decode(stream):
                    if frame.time is None or frame.time < targets[0] - 1e-3:
                        continue
                    if frame.time >= min(after, end):
                        break
                    picked.append(Frame(self._image(frame), frame.time))
                    targets = [t for t in targets if t > frame.time + 1e-3]
                    if not targets:
                        break
            except av.error.FFmpegError:
                continue
        return sorted(picked, key=lambda f: f.time)
