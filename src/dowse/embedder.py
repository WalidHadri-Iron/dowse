"""EmbeddingGemma 2 through llama-server's `/v1/embeddings`: text, images and audio, as 768-d L2-normalized vectors.

Images are sized here for their token budget exactly as the reference (transformers' Gemma 4 image processor) sizes
them, with Pillow's bicubic resampling, which is the reference's own: the server is started to take them as they are.
"""

import base64
import io
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

from . import modelfiles

DIM = 768
BUDGETS = (70, 140, 280, 560, 1120)


class Prompts:
    """EmbeddingGemma 2's task prompts (model card). Media takes no prompt."""

    @staticmethod
    def query(text: str) -> str:
        return f"task: search result | query: {text}"

    @staticmethod
    def code_query(text: str) -> str:
        return f"task: code retrieval | query: {text}"

    @staticmethod
    def document(text: str, title: str | None = None) -> str:
        return f"title: {title or 'none'} | text: {text}"


def target_size(height: int, width: int, budget: int) -> tuple[int, int] | None:
    """Fill `budget` soft tokens (each 3 × 3 patches of 16 px) keeping the aspect ratio, each side rounded down to a
    multiple of 48 px. None when the image is too thin to hold a single token."""
    patch, pooling = 16, 3
    max_patches = budget * pooling * pooling
    factor = (max_patches * patch * patch / (height * width)) ** 0.5
    side = pooling * patch
    target_height = int(factor * height / side) * side
    target_width = int(factor * width / side) * side
    if target_height == 0 and target_width == 0:
        return None
    max_side = (max_patches // (pooling * pooling)) * side
    if target_height == 0:
        target_height, target_width = side, min(width // height * side, max_side)
    elif target_width == 0:
        target_width, target_height = side, min(height // width * side, max_side)
    return target_height, target_width


def sized(image: Image.Image, budget: int) -> Image.Image:
    if budget not in BUDGETS:
        raise ValueError(f"budget must be one of {BUDGETS}")
    image = image.convert("RGB")
    size = target_size(image.height, image.width, budget)
    if size is None:
        raise ValueError(f"image too thin: {image.width}×{image.height}")
    height, width = size
    return image if (width, height) == image.size else image.resize((width, height), Image.Resampling.BICUBIC)


def image_part(image: Image.Image, budget: int, compact: bool = False) -> dict:
    """An image sized for `budget`, as a request part. BMP is lossless and costs nothing to encode; `compact` (PNG,
    also lossless) is for parts held in memory a while, like a long video's frames."""
    buffer = io.BytesIO()
    image_format = "PNG" if compact else "BMP"
    sized(image, budget).save(buffer, image_format, **({"compress_level": 1} if compact else {}))
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"content": [{"type": "image_url",
                         "image_url": {"url": f"data:image/{image_format.lower()};base64,{data}"}}]}


def audio_part(wav: bytes | Path) -> dict:
    """A 16 kHz mono WAV of at most 30 s (the model ignores the rest of one input), or the path of one."""
    data = wav.read_bytes() if isinstance(wav, Path) else wav
    return {"content": [{"type": "input_audio", "input_audio": {"data": base64.b64encode(data).decode("ascii"),
                                                                  "format": "wav"}}]}


class EmbedError(RuntimeError):
    pass


class Embedder:
    """A client of one llama-server. `parallel`: requests in flight for media (the server's slots)."""

    TEXT_BATCH = 16

    def __init__(self, url: str, parallel: int = 4, timeout: float = 300, *, model: str | modelfiles.Model | None = None):
        self.model = modelfiles.get(model)
        self.client = httpx.Client(base_url=url, timeout=timeout,
                                   limits=httpx.Limits(max_connections=parallel + 2))
        self.pool = ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="embed")

    def close(self) -> None:
        self.pool.shutdown()
        self.client.close()

    def __enter__(self) -> "Embedder":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def _post(self, inputs: list) -> np.ndarray:
        response = self.client.post("/v1/embeddings", json={"input": inputs})
        if response.status_code != 200:
            raise EmbedError(f"{response.status_code}: {response.text[:300]}")
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        vectors = np.asarray([item["embedding"] for item in data], dtype=np.float32)
        if vectors.shape != (len(inputs), DIM):
            raise EmbedError(f"expected {len(inputs)}×{DIM} vectors, got {vectors.shape}")
        if not np.isfinite(vectors).all():
            raise EmbedError("The model returned non-finite embeddings")
        return vectors

    def texts(self, texts: Sequence[str]) -> np.ndarray:
        """Texts already prompted (`Prompts`): one row each."""
        if not texts:
            return np.zeros((0, DIM), np.float32)
        batches = [list(texts[i:i + self.TEXT_BATCH]) for i in range(0, len(texts), self.TEXT_BATCH)]
        return np.concatenate(list(self.pool.map(self._post, batches)))

    def query(self, text: str) -> np.ndarray:
        return self._post([Prompts.query(text)])[0]

    def images(self, images: Sequence[Image.Image], budget: int) -> list[np.ndarray | Exception | None]:
        """One vector per image, or the error that image hit (one bad file mustn't sink its neighbours)."""
        if self.model.media is None:
            return [None] * len(images)
        return self.media([image_part(image, budget) for image in images])

    def audio(self, wavs: Sequence[bytes | Path]) -> list[np.ndarray | Exception | None]:
        if self.model.media is None:
            return [None] * len(wavs)
        return self.media([audio_part(wav) for wav in wavs])

    def media(self, parts: list[dict]) -> list[np.ndarray | Exception | None]:
        """Request parts (`image_part`, `audio_part`) embedded in parallel: a vector each, or the error it hit."""
        if self.model.media is None:
            return [None] * len(parts)   # text-only models never receive unsupported image/audio requests
        def one(part: dict) -> np.ndarray | Exception:
            try:
                return self._post([part])[0]
            except (EmbedError, httpx.HTTPError) as error:
                return error

        return list(self.pool.map(one, parts))
