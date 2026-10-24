"""What the app asks of the system: opening a file where it matched, showing it in Explorer (Finder), putting it on
the clipboard (the file, as Explorer copies it, or its path), and reading the user's theme and accent color. On a Mac
each is `macapi`'s."""

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path, PurePath

from . import winapi
from ..rules import Kind

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"


# Opening

def open_command(path: str, kind: Kind, loc: float | None, opener: str | None) -> list[str] | None:
    """The command that opens the file where it matched (a PDF's page, a recording's moment) with `opener`, the
    program Windows opens such files with; None when that program can't be told where (then the file just opens)."""
    if opener is None or loc is None:
        return None
    program = PurePath(opener).name.lower()
    if kind is Kind.PDF:
        page = int(loc)
        if program in ("msedge.exe", "chrome.exe", "brave.exe", "firefox.exe"):
            return [opener, Path(path).as_uri() + f"#page={page}"]
        if program in ("acrobat.exe", "acrord32.exe"):
            return [opener, "/A", f"page={page}", path]
        if program.startswith("sumatrapdf"):
            return [opener, "-reuse-instance", "-page", str(page), path]
        return None
    if kind in (Kind.AUDIO, Kind.VIDEO):
        seconds = max(int(loc), 0)
        if program == "vlc.exe":
            return [opener, f"--start-time={seconds}", path]
        if program == "mpv.exe":
            return [opener, f"--start={seconds}", path]
        if program.startswith("potplayer"):
            return [opener, path, f"/seek={seconds}"]
        if program.startswith(("mpc-hc", "mpc-be")):
            return [opener, path, "/start", str(seconds * 1000)]
    return None


def open_file(path: str, kind: Kind, loc: float | None = None) -> None:
    if MAC:
        from . import macapi
        return macapi.open_file(path, kind.value, loc)
    command = open_command(path, kind, loc, winapi.default_app(PurePath(path).suffix))
    if command:
        subprocess.Popen(command, close_fds=True)
    else:
        os.startfile(path)  # noqa: S606 (the file's own app, as a double-click would)


def reveal(path: str) -> None:
    """Explorer, with the file selected."""
    if MAC:
        from . import macapi
        return macapi.reveal(path)
    subprocess.Popen(f'explorer.exe /select,"{os.path.normpath(path)}"')


# The clipboard

CF_UNICODETEXT, CF_HDROP, GMEM_MOVEABLE = 13, 15, 0x0002


def _set_clipboard(fmt: int, data: bytes) -> None:
    kernel32, user32 = ctypes.WinDLL("kernel32"), ctypes.WinDLL("user32")
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    for _ in range(25):   # clipboard watchers (Windows' own history among them) hold it a moment after each copy
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree(handle)
        raise OSError("the clipboard is in use")
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(fmt, handle)   # the clipboard owns the memory from here
    finally:
        user32.CloseClipboard()


def copy_path(path: str) -> None:
    if MAC:
        from . import macapi
        return macapi.copy_path(path)
    _set_clipboard(CF_UNICODETEXT, (path + "\0").encode("utf-16-le"))


def copy_file(path: str) -> None:
    """The file itself, as Explorer's Copy puts it: paste it into a folder, a chat, an email."""
    if MAC:
        from . import macapi
        return macapi.copy_file(path)
    files = (os.path.normpath(path) + "\0\0").encode("utf-16-le")
    header = (20).to_bytes(4, "little") + bytes(8) + bytes(4) + (1).to_bytes(4, "little")   # DROPFILES, wide names
    _set_clipboard(CF_HDROP, header + files)


# The user's look

def theme() -> dict:
    """{"dark": bool, "accent": "#rrggbb"}: Windows' app theme and accent color (a Mac's appearance and accent)."""
    if MAC:
        from . import macapi
        return macapi.theme()
    dark, accent = False, "#0067c0"
    if WINDOWS:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                dark = winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as key:
                abgr = winreg.QueryValueEx(key, "AccentColor")[0]
                accent = f"#{abgr & 0xFF:02x}{(abgr >> 8) & 0xFF:02x}{(abgr >> 16) & 0xFF:02x}"
        except OSError:
            pass
    return {"dark": dark, "accent": accent}
