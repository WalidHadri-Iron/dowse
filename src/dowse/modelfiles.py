"""Supported local embedding models, pinned and checksummed. Model choice is an index identity, not a filename.

Pinned (and checksummed) so each index is made by one known set of weights. Never the F16 files: the model's activations
overflow float16.
"""

from dataclasses import dataclass
from pathlib import Path

from . import paths, settings
from .download import Progress, fetch, sha256

REPOSITORY = "ggml-org/embeddinggemma-2-GGUF"
REVISION = "bfcd298762cc34d0357ece5ebdd31791a3a374d8"


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str
    repository: str = REPOSITORY
    revision: str = REVISION

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{self.name}"


# The text model (310 MB): all that queries need.
TEXT = ModelFile("embeddinggemma-2-Q8_0.gguf", 309_855_456,
                 "2188ac1deca4b77dffefd603c2776a9d76d9d74ec01841392982ebb840b09135")
# The image and audio encoders (555 MB).
MEDIA = ModelFile("mmproj-embeddinggemma-2-Q8_0.gguf", 554_821_024,
                  "c4a8a52691ecef40618438928bdf9e68379b854e24166f292592353db0aab64f")
ALL = (TEXT, MEDIA)

DEFAULT = "embeddinggemma-2-q8"


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    description: str
    text: ModelFile
    media: ModelFile | None = None

    @property
    def files(self) -> tuple[ModelFile, ...]:
        return (self.text, self.media) if self.media else (self.text,)

    @property
    def vector_tag(self) -> str:
        # Keep the original identity: existing installations must not reindex just to gain a selector.
        suffix = "" if self.id == DEFAULT else f"/{self.text.name}"
        return f"{self.text.repository}@{self.text.revision[:7]}{suffix}/v1"

    def json(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description,
                "bytes": sum(f.size for f in self.files), "installed": not missing(model=self),
                "multimodal": self.media is not None, "recommended": self.id == DEFAULT}


MODELS = (
    Model(DEFAULT, "EmbeddingGemma 2 · Q8", "Full file search: text, photos, screenshots, PDFs, sound and video.",
          TEXT, MEDIA),
    Model("embeddinggemma-300m-q4", "EmbeddingGemma 300M · Q4",
          "Smaller, text-only model. Documents and PDF text have semantic search; photos, sound and video use filenames/keywords only.",
          ModelFile("embeddinggemma-300M-qat-Q4_0.gguf", 277_852_192,
                    "50d28e22432a148f6f8a86eab3700f92add5d1f54baf7790675a2a4dadbccf26",
                    "ggml-org/embeddinggemma-300M-qat-q4_0-GGUF", "8dd0ca2a66a8f14470acb0e2a71f801afbc5fb73")),
)


def active_id() -> str:
    """The index's committed choice wins after a crash between the DB commit and saving settings."""
    import sqlite3
    from .store import default_file

    if default_file().exists():
        # Model lookup is read-only: it must not migrate an index owned by an older, still-running app.
        reader = sqlite3.connect(default_file().as_uri() + "?mode=ro", uri=True)
        try:
            meta = dict(reader.execute("SELECT key, value FROM meta WHERE key IN ('search_model', 'vectors')"))
            if selected := meta.get("search_model"):
                return selected
            tag = meta.get("vectors")
            if tag:
                for model in MODELS:
                    if model.vector_tag == tag:
                        return model.id   # legacy indexes have the vector identity but no explicit model id
        finally:
            reader.close()
    return settings.load().get("search_model", DEFAULT)


def get(model: str | Model | None = None) -> Model:
    if isinstance(model, Model):
        return model
    wanted = model if model is not None else active_id()
    for candidate in MODELS:
        if candidate.id == wanted:
            return candidate
    raise ValueError("Unknown search model. Choose a supported model.")


def path(file: ModelFile, folder: Path | None = None) -> Path:
    return (folder or paths.models()) / file.name


def missing(folder: Path | None = None, *, model: str | Model | None = None) -> list[ModelFile]:
    return [file for file in get(model).files if not path(file, folder).exists()]


def fetch_one(file: ModelFile, folder: Path | None = None, progress: Progress | None = None) -> Path:
    """Downloads one model file (resuming a partial one), checked against its size and checksum."""
    return fetch(file.url, path(file, folder), size=file.size, checksum=file.sha256, progress=progress)


def download(folder: Path | None = None, progress: Progress | None = None, *, model: str | Model | None = None) -> None:
    for file in missing(folder, model=model):
        fetch_one(file, folder, progress)


def damaged(model: str | Model | None = None) -> list[ModelFile]:
    """The model's files that are here but aren't the files they should be (a disk error, an edit, a bad copy)."""
    return [file for file in get(model).files
            if path(file).exists() and (path(file).stat().st_size != file.size or sha256(path(file)) != file.sha256)]


def verify(model: str | Model | None = None) -> None:
    for file in get(model).files:
        if not path(file).exists():
            raise OSError(f"{file.name} is missing. Download it before switching.")
    for file in damaged(model):
        raise OSError(f"{file.name} does not match the supported model's checksum. Download a clean copy before switching.")
