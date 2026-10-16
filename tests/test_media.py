"""Audio windows and video frames, on media made here (no model needed)."""

import io
import wave
from fractions import Fraction

import av
import numpy as np
import pytest
from PIL import Image

from dowse.extract import ExtractError, media


def write_audio(path, seconds: float, rate: int = 44100, silent_from: float | None = None, codec="pcm_s16le"):
    """A tone whose pitch rises with time, so windows from different moments differ."""
    t = np.arange(int(seconds * rate)) / rate
    samples = (0.3 * np.sin(2 * np.pi * (300 + 5 * t) * t) * 32767).astype(np.int16)
    if silent_from is not None:
        samples[int(silent_from * rate):] = 0
    with av.open(str(path), "w") as container:
        stream = container.add_stream(codec, rate=rate, layout="mono")
        for start in range(0, len(samples), 1024):
            frame = av.AudioFrame.from_ndarray(samples[None, start:start + 1024], format="s16", layout="mono")
            frame.sample_rate, frame.pts, frame.time_base = rate, start, Fraction(1, rate)
            container.mux(stream.encode(frame))
        container.mux(stream.encode(None))


def write_video(path, colors: list[tuple[int, int, int]], seconds_each: int = 6, fps: int = 5, gop: int = 1000):
    """A shot per color, each half black so the shots differ in shape, not only in brightness."""
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 320, 240, "yuv420p"
        stream.codec_context.gop_size = gop
        index = 0
        for shot, color in enumerate(colors):
            image = Image.new("RGB", (320, 240), color)
            image.paste((0, 0, 0), (0, 0, 160, 240) if shot % 2 else (0, 0, 320, 120))
            for _ in range(seconds_each * fps):
                frame = av.VideoFrame.from_image(image)
                frame.pts = index
                container.mux(stream.encode(frame))
                index += 1
        container.mux(stream.encode(None))


def samples(window: media.Window) -> np.ndarray:
    with wave.open(io.BytesIO(window.wav)) as clip:
        assert (clip.getframerate(), clip.getnchannels()) == (16000, 1)
        return np.frombuffer(clip.readframes(clip.getnframes()), np.int16)


def test_windows_overlap_and_cover_the_tail(tmp_path):
    write_audio(tmp_path / "talk.wav", 70)
    windows = list(media.sound(str(tmp_path / "talk.wav")))
    assert [(w.start, w.end) for w in windows] == [(0, 30), (25, 55), (50, 70)]
    assert len(samples(windows[0])) == 30 * 16000


def test_a_resumed_read_makes_the_same_windows(tmp_path):
    # mp3: a compressed format whose seeks land between frames, not on the sample asked for.
    write_audio(tmp_path / "talk.mp3", 130, codec="libmp3lame")
    whole = list(media.sound(str(tmp_path / "talk.mp3")))
    resumed = list(media.sound(str(tmp_path / "talk.mp3"), start=50))
    assert [(w.start, w.end) for w in resumed] == [(w.start, w.end) for w in whole if w.start >= 50]
    for a, b in zip(resumed, [w for w in whole if w.start >= 50]):
        x, y = samples(a).astype(float), samples(b).astype(float)
        assert np.corrcoef(x, y)[0, 1] > 0.99   # the same sound, give or take the decoder's warm-up


def test_silence_and_short_files(tmp_path):
    write_audio(tmp_path / "half.wav", 60, silent_from=20)
    assert [w.start for w in media.sound(str(tmp_path / "half.wav"))] == [0]   # 25–55 and the tail are silent
    write_audio(tmp_path / "blip.wav", 1.5)
    assert list(media.sound(str(tmp_path / "blip.wav"))) == []                 # under 2 s
    write_audio(tmp_path / "short.mp3", 8, codec="libmp3lame")
    windows = list(media.sound(str(tmp_path / "short.mp3")))
    assert len(windows) == 1 and windows[0].end == pytest.approx(8, abs=0.2)


def test_not_media(tmp_path):
    (tmp_path / "fake.mp3").write_bytes(b"this is not audio" * 100)
    with pytest.raises(ExtractError):
        list(media.sound(str(tmp_path / "fake.mp3")))


def test_shots_get_a_frame_each(tmp_path):
    write_video(tmp_path / "clip.mp4", [(200, 30, 30), (30, 200, 30), (30, 30, 200)])
    with media.FrameReader(str(tmp_path / "clip.mp4")) as reader:
        assert reader.duration == pytest.approx(18, abs=0.5) and not reader.has_audio and reader.size == (320, 240)
        hashes = [media.average_hash(f.image) for f in reader.frames(0, reader.duration, spacing=3)]
        kept = [h for i, h in enumerate(hashes) if i == 0 or not media.same_shot(hashes[i - 1], h)]
        assert len(kept) == 3


def test_frames_between_far_apart_keyframes(tmp_path):
    # One 40 s shot, one keyframe: the reader seeks into the gap for a frame every 3 s.
    write_video(tmp_path / "long.mp4", [(120, 120, 200)], seconds_each=40)
    with media.FrameReader(str(tmp_path / "long.mp4")) as reader:
        whole = [round(f.time, 1) for f in reader.frames(0, reader.duration, spacing=3)]
        assert whole == [float(t) for t in range(0, 37, 3)]
        # Read in stretches, it gets the same frames: gaps are measured between keyframes, not from step edges.
        stepped = [round(f.time, 1) for a in range(0, 40, 10) for f in reader.frames(a, a + 10, spacing=3)]
        assert stepped == whole


def test_average_hash_separates_shots():
    left = Image.new("RGB", (64, 64), "white")
    left.paste((0, 0, 0), (0, 0, 32, 64))
    top = Image.new("RGB", (64, 64), "white")
    top.paste((0, 0, 0), (0, 0, 64, 32))
    assert media.same_shot(media.average_hash(left), media.average_hash(left.copy()))
    assert not media.same_shot(media.average_hash(left), media.average_hash(top))


def test_a_video_of_keyframes_only_makes_pictures_of_the_frames_kept(tmp_path, monkeypatch):
    """Intra-only footage (every frame a keyframe): a picture per frame wanted, never one per frame (a 4K one held
    25,000 of them), the same frames read whole or a stretch at a time."""
    write_video(tmp_path / "intra.mp4", [(200, 40, 40), (40, 200, 40), (40, 40, 200)], seconds_each=20, fps=10, gop=1)
    made = []
    real = media.FrameReader._image
    monkeypatch.setattr(media.FrameReader, "_image", lambda self, frame: made.append(frame.time) or real(self, frame))
    with media.FrameReader(str(tmp_path / "intra.mp4")) as reader:
        whole = reader.frames(0, 60, spacing=3)
        assert [round(f.time, 1) for f in whole] == [float(t) for t in range(0, 60, 3)]
        assert len(made) == len(whole)   # nothing made a picture only to be dropped
        halves = reader.frames(0, 30, spacing=3) + reader.frames(30, 60, spacing=3)
    assert [round(f.time, 1) for f in halves] == [round(f.time, 1) for f in whole]
