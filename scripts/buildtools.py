"""What the app builders share (`build_installer.py` on Windows, `build_mac_app.py` on a Mac): pinned downloads,
the version, uv."""

import hashlib
import os
import subprocess
import tomllib
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def say(text: str) -> None:
    print(text, flush=True)


def download(url: str, target: Path, sha256: str | None = None) -> Path:
    if not target.exists():
        say(f"  downloading {url}")
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with part.open("wb") as out:
                for chunk in response.iter_bytes(1 << 20):
                    out.write(chunk)
        part.replace(target)
    if sha256:
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != sha256:
            target.unlink()
            raise SystemExit(f"{target.name}: sha256 {actual} isn't the pinned {sha256}")
    return target


def version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    package = (ROOT / "src" / "dowse" / "__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{project}"' not in package:
        raise SystemExit(f"pyproject.toml says {project}, but dowse.__version__ differs: make them agree")
    return project


def uv(*args: str) -> None:
    command = [os.environ.get("UV", "uv"), *args]
    say("  " + " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)
