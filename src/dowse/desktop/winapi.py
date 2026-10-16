"""The few Windows calls the app needs: a global hotkey, starting with Windows, which app opens a file type, and the
app's identity on the taskbar. Each degrades to "not available" off Windows, so the app still imports anywhere."""

import contextlib
import ctypes
import os
import sys
from ctypes import wintypes

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
_MODIFIERS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backquote": 0xC0, "`": 0xC0}
_KEYS |= {f"f{n}": 0x6F + n for n in range(1, 13)}
_KEYS |= {chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)} | {str(d): 0x30 + d for d in range(10)}

WINDOWS = sys.platform == "win32"


def parse_hotkey(text: str) -> tuple[int, int]:
    """"Alt+Shift+Space" → (modifiers, virtual key). Raises ValueError for what can't be a hotkey."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    modifiers = 0
    for part in parts[:-1]:
        if part not in _MODIFIERS:
            raise ValueError(f"not a modifier: {part}")
        modifiers |= _MODIFIERS[part]
    if parts[-1] not in _KEYS:
        raise ValueError(f"not a key: {parts[-1]}")
    if not modifiers:
        raise ValueError("a hotkey needs Alt, Ctrl, Shift or Win")
    return modifiers, _KEYS[parts[-1]]


def register_hotkey(window_handle: int, ident: int, text: str) -> bool:
    """Registers `text` for the window: Windows then sends it WM_HOTKEY with `ident`. False if another app has it."""
    if not WINDOWS:
        return False
    modifiers, key = parse_hotkey(text)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    return bool(user32.RegisterHotKey(wintypes.HWND(window_handle), ident, modifiers | MOD_NOREPEAT, key))


def unregister_hotkey(window_handle: int, ident: int) -> None:
    if WINDOWS:
        ctypes.WinDLL("user32").UnregisterHotKey(wintypes.HWND(window_handle), ident)


def hotkey_message(message_address: int, ident: int) -> bool:
    """Is the native message at `message_address` (from a QWidget's nativeEvent) our hotkey?"""
    message = wintypes.MSG.from_address(message_address)
    return message.message == WM_HOTKEY and message.wParam == ident


def dark_title_bar(window_handle: int, dark: bool) -> None:
    """Windows draws title bars light unless asked (attribute 20; 19 before Windows 10 20H1)."""
    if not WINDOWS:
        return
    value = ctypes.c_int(1 if dark else 0)
    dwmapi = ctypes.WinDLL("dwmapi")
    for attribute in (20, 19):
        if dwmapi.DwmSetWindowAttribute(wintypes.HWND(window_handle), attribute, ctypes.byref(value), 4) == 0:
            return


def set_app_id(app_id: str) -> None:
    """Groups the app's windows and notifications under its own name and icon, not python.exe's."""
    if WINDOWS:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


# Starting with Windows (the current user's Run key: no admin rights, removed with the app)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "Dowse"


APP_EXE = "Dowse.exe"   # the installed app's own program (its Python, named and iconed for Dowse)


def launcher() -> tuple[str, list[str]]:
    """How to start this app again, as (program, arguments): the installed app's Dowse.exe; a launcher it was started
    by (`dowse-app.exe` in a development venv); else pythonw (no console window) with the module."""
    folder = os.path.dirname(sys.executable)
    if os.path.exists(os.path.join(folder, APP_EXE)):
        return os.path.join(folder, APP_EXE), ["-m", "dowse.desktop.launch"]
    if sys.argv and sys.argv[0].lower().endswith(".exe"):
        return os.path.abspath(sys.argv[0]), []
    pythonw = os.path.join(folder, "pythonw.exe")
    return (pythonw if os.path.exists(pythonw) else sys.executable), ["-m", "dowse.desktop.launch"]


def start_again() -> None:
    """Starts this app again once this process has gone (it waits for the startup lock: `launch --restart`)."""
    import subprocess

    program, arguments = launcher()
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([program, *arguments, "--restart"], creationflags=flags, close_fds=True)


def launch_command() -> str:
    program, arguments = launcher()
    return " ".join([f'"{program}"', *arguments])


def starts_with_windows() -> bool:
    if not WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_VALUE)
            return True
    except OSError:
        return False


def set_starts_with_windows(enabled: bool) -> None:
    if not WINDOWS:
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, launch_command() + " --background")
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, _RUN_VALUE)


# File types

def default_app(extension: str) -> str | None:
    """The program that opens `extension` (".pdf") when you double-click such a file, or None (a Store app, or no
    association)."""
    if not WINDOWS:
        return None
    shlwapi = ctypes.WinDLL("shlwapi")
    size = wintypes.DWORD(1024)
    buffer = ctypes.create_unicode_buffer(size.value)
    ASSOCSTR_EXECUTABLE = 2
    if shlwapi.AssocQueryStringW(0, ASSOCSTR_EXECUTABLE, extension, "open", buffer, ctypes.byref(size)) != 0:
        return None
    path = buffer.value
    return path if path and os.path.exists(path) else None


# Where the user's folders are (they can be moved, or redirected to OneDrive), and power

_KNOWN_FOLDERS = {
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}", "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}", "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def known_folder(name: str) -> str:
    """The real location of the user's Desktop, Documents, Downloads, Pictures, Videos or Music."""
    fallback = os.path.join(os.path.expanduser("~"), name)
    if not WINDOWS:
        return fallback
    guid = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(_KNOWN_FOLDERS[name], ctypes.byref(guid))
    path = ctypes.c_wchar_p()
    try:
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path))
        return path.value or fallback
    except OSError:
        return fallback
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)


class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


def power() -> tuple[bool, bool]:
    """(on battery, Battery Saver on). A desktop without a battery is never on battery."""
    if not WINDOWS:
        return False, False
    status = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return False, False
    return status.ACLineStatus == 0, status.SystemStatusFlag == 1
