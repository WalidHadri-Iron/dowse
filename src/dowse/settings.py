"""The user's choices that aren't the index: the folders to index, the hotkey. One JSON file beside the index."""

import json
from pathlib import Path

from . import paths

DEFAULTS = {"folders": [], "hotkey": "Alt+Shift+Space"}


def file() -> Path:
    return paths.home() / "settings.json"


def load() -> dict:
    try:
        return DEFAULTS | json.loads(file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(settings: dict) -> None:
    file().parent.mkdir(parents=True, exist_ok=True)
    temporary = file().with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(file())   # never a half-written settings file
