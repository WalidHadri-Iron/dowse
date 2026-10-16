"""Encrypting what Dowse keeps about your files (the index, with text read from them, and the logs) with Windows'
own file encryption (EFS): tied to your Windows sign-in, so a copy of the files, or the disk in another PC, can't be
read. Nothing to remember and nothing slower: Windows decrypts for you, as you.

EFS can't encrypt a file that's open, so it's applied when the app starts, before anything opens the index (`apply`),
and a change of the setting restarts the app. It isn't in Windows Home: there, Device encryption or BitLocker
encrypts the whole drive instead. If someone else resets your Windows password (not you changing it), the encrypted
index can't be read any more: it's set aside and built again (`apply` checks).
"""

import contextlib
import ctypes
import logging
import os
import sys
import time
from pathlib import Path

from . import paths

log = logging.getLogger("dowse.privacy")

FILE_ATTRIBUTE_ENCRYPTED = 0x4000
WINDOWS = sys.platform == "win32"


def folders() -> list[Path]:
    """What's encrypted: the index (paths, words read from files, vectors) and the logs (file paths)."""
    return [paths.index(), paths.logs()]


def encrypted(path: Path) -> bool:
    if not WINDOWS:
        return False
    get = ctypes.windll.kernel32.GetFileAttributesW
    get.restype = ctypes.c_uint32
    attributes = get(str(path))
    return attributes != 0xFFFFFFFF and bool(attributes & FILE_ATTRIBUTE_ENCRYPTED)


def _advapi():
    return ctypes.WinDLL("advapi32", use_last_error=True)


def _encrypt(path: Path, on: bool) -> bool:
    advapi = _advapi()
    done = advapi.EncryptFileW(str(path)) if on else advapi.DecryptFileW(str(path), 0)
    if not done:   # in use (another Dowse has the index open): tried again at the next start
        log.warning("%s %s: Windows error %s", "encrypting" if on else "decrypting", path, ctypes.get_last_error())
    return bool(done)


def supported(where: Path | None = None) -> bool:
    """Can this Windows encrypt files there? (Not on Home; not on a FAT drive.) Tried on a scratch folder."""
    if not WINDOWS:
        return False
    probe = (where or paths.home()) / f".encryption-test-{os.getpid()}"
    try:
        probe.mkdir(parents=True, exist_ok=True)
        works = bool(_advapi().EncryptFileW(str(probe)))
        if works:
            _advapi().DecryptFileW(str(probe), 0)
        return works
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            probe.rmdir()


def apply(on: bool, places: list[Path] | None = None) -> None:
    """Encrypts (or decrypts) the folders and every file in them. At start, before any of them is opened. A
    file that can't be read as this user any more (encrypted under a password that was reset) is set aside."""
    if not WINDOWS:
        return
    for folder in places or folders():
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)
        for file in [p for p in folder.rglob("*") if p.is_file()]:
            if encrypted(file) and not _readable(file):
                aside = file.with_name(f"{file.name}.unreadable-{time.strftime('%Y%m%d-%H%M%S')}")
                log.warning("%s can't be read as you (encrypted under a reset password?): set aside", file)
                file.replace(aside)
                continue
            if encrypted(file) != on:
                _encrypt(file, on)
        if encrypted(folder) != on:   # a folder marked so: what's made in it later is encrypted too
            _encrypt(folder, on)


def _readable(file: Path) -> bool:
    try:
        with open(file, "rb") as handle:
            handle.read(1)
        return True
    except PermissionError:
        return False
    except OSError:
        return True   # in use, or something else: not this problem
