"""Where Dowse keeps its things: %LOCALAPPDATA%\\Dowse on Windows, or $DOWSE_HOME."""

import os
import sys
from pathlib import Path


def home() -> Path:
    if chosen := os.environ.get("DOWSE_HOME"):
        return Path(chosen)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Dowse"
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


