"""Where Dowse keeps its things: %LOCALAPPDATA%\\Dowse on Windows, ~/Library/Application Support/Dowse on a Mac,
$XDG_DATA_HOME/dowse elsewhere, or $DOWSE_HOME."""

import os
import sys
import unicodedata
from pathlib import Path


def home() -> Path:
    if chosen := os.environ.get("DOWSE_HOME"):
        return Path(chosen)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Dowse"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Dowse"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "dowse"


def models() -> Path:
    """The supported models' GGUF files (`modelfiles`)."""
    return home() / "Models"


def runtimes() -> Path:
    """llama.cpp builds, one folder per release and backend (`runtime`)."""
    return home() / "Runtime"


def index() -> Path:
    """The index database (`store`)."""
    return home() / "Index"


def logs() -> Path:
    return home() / "Logs"




def fold(path: str) -> str:
    """A path as the system compares it. Windows ignores letter case (and takes either slash); so does a Mac's disk by
    default, which also ignores how an accent is written (é as one character, or e and an accent). Elsewhere a path
    is compared as written."""
    if sys.platform == "win32":
        return os.path.normcase(path)
    if sys.platform == "darwin":
        return unicodedata.normalize("NFC", path).lower()
    return path
