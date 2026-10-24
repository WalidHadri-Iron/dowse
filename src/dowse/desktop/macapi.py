"""What the app asks of macOS: the global shortcut's keys, starting at login, opening a file where it matched,
showing it in Finder, the clipboard, the look (dark mode, accent color), power, and where screenshots are saved.
`winapi` and `system` hand over to these on a Mac, so the rest of the app asks the same questions everywhere.

AppKit comes through PyObjC (installed on Macs with the window's own pywebview); a few answers come from macOS's
command-line tools (`open`, `osascript`, `pmset`, `defaults`), as the system itself provides them.
"""

import os
import plistlib
import subprocess
from pathlib import Path

# The shortcut, as Carbon's RegisterEventHotKey takes it (it needs no Accessibility permission)
CMD, SHIFT, OPTION, CONTROL = 0x0100, 0x0200, 0x0800, 0x1000
_MODIFIERS = {"alt": OPTION, "option": OPTION, "opt": OPTION, "ctrl": CONTROL, "control": CONTROL,
              "shift": SHIFT, "cmd": CMD, "command": CMD, "win": CMD}
_KEYS = {"space": 49, "enter": 36, "return": 36, "tab": 48, "esc": 53, "backquote": 50, "`": 50}
_KEYS |= dict(zip([f"f{n}" for n in range(1, 13)], [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111]))
_KEYS |= {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
          "e": 14, "r": 15, "y": 16, "t": 17, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45,
          "m": 46}
_KEYS |= {"1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29}


def parse_hotkey(text: str) -> tuple[int, int]:
    """"Alt+Shift+Space" → (Carbon modifiers, virtual key code). Alt is the Option key, Win the Command key. Raises
    ValueError for what can't be a shortcut."""
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
        raise ValueError("a shortcut needs Option, Control, Shift or Command")
    return modifiers, _KEYS[parts[-1]]


def startup_error(message: str) -> None:
    """A failure before the app (and its log) can open: said in a dialog, as on Windows."""
    script = f'display alert "Dowse couldn\'t start" message {_quoted(message)} as critical'
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=600)


def notify(title: str, message: str) -> None:
    subprocess.Popen(["osascript", "-e", f"display notification {_quoted(message)} with title {_quoted(title)}"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _quoted(text: str) -> str:
    """`text` as an AppleScript string."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Starting at login: a LaunchAgent of the user's (no admin rights; removed with the app)

AGENT = "app.dowse.login"


def _agent_file() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{AGENT}.plist"


def starts_at_login() -> bool:
    return _agent_file().exists()


def set_starts_at_login(enabled: bool, program: str, arguments: list[str]) -> None:
    agent = _agent_file()
    if not enabled:
        agent.unlink(missing_ok=True)
        return
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_bytes(plistlib.dumps({"Label": AGENT, "ProgramArguments": [program, *arguments, "--background"],
                                      "RunAtLoad": True, "ProcessType": "Interactive"}))


# Opening a file where it matched

def default_app(path: str) -> str | None:
    """The app (its bundle's path) that opens `path` on a double-click, or None."""
    try:
        from AppKit import NSWorkspace
        from Foundation import NSURL
    except ImportError:
        return None
    app = NSWorkspace.sharedWorkspace().URLForApplicationToOpenURL_(NSURL.fileURLWithPath_(path))
    return str(app.path()) if app is not None else None


def open_command(path: str, kind: str, loc: float | None, app: str | None) -> list[str] | None:
    """The command that opens the file where it matched (a PDF's page, a recording's moment) with `app` (a bundle
    path), or None when that app can't be told where (then the file just opens)."""
    if app is None or loc is None:
        return None
    name = Path(app).stem.lower()
    if kind == "pdf":
        if name in ("google chrome", "microsoft edge", "brave browser", "firefox", "arc"):
            return ["open", "-a", app, Path(path).as_uri() + f"#page={int(loc)}"]
        return None
    if kind in ("audio", "video"):
        seconds = max(int(loc), 0)
        if name == "vlc":
            return [str(Path(app) / "Contents" / "MacOS" / "VLC"), f"--start-time={seconds}", path]
        if name == "iina":
            return ["open", "-a", app, "--args", f"--mpv-start={seconds}", path]
        if name == "quicktime player":   # the Mac's own player: told the time by AppleScript
            return ["osascript", "-e", f"tell application \"QuickTime Player\" to open POSIX file {_quoted(path)}",
                    "-e", f"tell application \"QuickTime Player\" to set current time of front document to {seconds}",
                    "-e", "tell application \"QuickTime Player\" to play front document"]
    return None


def open_file(path: str, kind: str, loc: float | None = None) -> None:
    command = open_command(path, kind, loc, default_app(path))
    subprocess.Popen(command or ["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_folder(path: str) -> None:
    subprocess.Popen(["open", path])


def reveal(path: str) -> None:
    """Finder, with the file selected."""
    subprocess.Popen(["open", "-R", path])


# The clipboard

def copy_path(path: str) -> None:
    from AppKit import NSPasteboard, NSPasteboardTypeString

    board = NSPasteboard.generalPasteboard()
    board.clearContents()
    board.setString_forType_(path, NSPasteboardTypeString)


def copy_file(path: str) -> None:
    """The file itself, as Finder's Copy puts it: paste it into a folder, a message, a mail."""
    from AppKit import NSPasteboard
    from Foundation import NSURL

    board = NSPasteboard.generalPasteboard()
    board.clearContents()
    board.writeObjects_([NSURL.fileURLWithPath_(path)])


# The look, power, folders

def theme() -> dict:
    """{"dark": bool, "accent": "#rrggbb"}: macOS's appearance and accent color."""
    dark, accent = False, "#007aff"
    try:
        from AppKit import NSColor, NSColorSpace
        from Foundation import NSUserDefaults

        dark = NSUserDefaults.standardUserDefaults().stringForKey_("AppleInterfaceStyle") == "Dark"
        color = NSColor.controlAccentColor().colorUsingColorSpace_(NSColorSpace.sRGBColorSpace())
        if color is not None:
            accent = "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in
                                                    (color.redComponent(), color.greenComponent(), color.blueComponent())))
    except ImportError:
        pass
    return {"dark": dark, "accent": accent}


def power() -> tuple[bool, bool]:
    """(on battery, Low Power Mode on)."""
    try:
        battery = "Battery Power" in subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True,
                                                    timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        battery = False
    try:
        from Foundation import NSProcessInfo

        saver = bool(NSProcessInfo.processInfo().isLowPowerModeEnabled())
    except (ImportError, AttributeError):
        saver = False
    return battery, saver


def known_folder(name: str) -> str:
    """The user's Desktop, Documents, Downloads, Pictures, Videos (a Mac's Movies) or Music."""
    return os.path.join(os.path.expanduser("~"), {"Videos": "Movies"}.get(name, name))


def screenshots_folder() -> str:
    """Where macOS saves screenshots: the folder chosen in the Screenshot app's Options, else the Desktop."""
    try:
        chosen = subprocess.run(["defaults", "read", "com.apple.screencapture", "location"], capture_output=True,
                                text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        chosen = ""
    chosen = os.path.expanduser(chosen) if chosen else ""
    return chosen if chosen and os.path.isdir(chosen) else known_folder("Desktop")

