"""Resumable, verified downloads: a file gets its final name only once its size (and checksum, when known) match.
A partial file from an interrupted try is resumed; one that can't be right (longer than the file, or complete but
wrong) is thrown away, so the next try starts clean instead of failing the same way forever."""

import hashlib
from collections.abc import Callable
from pathlib import Path

import httpx

Progress = Callable[[int, int], None]   # (bytes so far, total bytes or 0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(8 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, to: Path, *, size: int | None = None, checksum: str | None = None,
          progress: Progress | None = None) -> Path:
    """Downloads `url` to `to`, resuming a partial `.part` file from an earlier try. Does nothing if `to` is there."""
    if to.exists():
        return to
    to.parent.mkdir(parents=True, exist_ok=True)
    part = to.with_name(to.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if size is not None and have > size:
        part.unlink()
        have = 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as response:
        if response.status_code == 416:      # the part file is already complete (or isn't this file: below)
            if size is not None and have != size:
                part.unlink()
                raise OSError(f"{to.name}: the partial download didn't match; it starts again next time")
        else:
            response.raise_for_status()
            if have and response.status_code != 206:   # the server ignored the range: start over
                have = 0
            total = have + int(response.headers.get("Content-Length", 0))
            with part.open("ab" if have else "wb") as out:
                for chunk in response.iter_bytes(1 << 20):
                    out.write(chunk)
                    have += len(chunk)
                    if progress:
                        progress(have, total)
    if size is not None and part.stat().st_size != size:
        got = part.stat().st_size
        if got > size:
            part.unlink()
        raise OSError(f"{to.name}: got {got} bytes, expected {size}")
    if checksum is not None and (actual := sha256(part)) != checksum:
        part.unlink()
        raise OSError(f"{to.name}: checksum {actual[:12]}… doesn't match {checksum[:12]}…")
    part.replace(to)
    return to
