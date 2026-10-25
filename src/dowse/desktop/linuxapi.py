"""What the app asks of Linux, the same questions `macapi` answers on a Mac: a startup failure said in a dialog,
notifications, starting at login, opening a file where it matched, showing it in the file manager, the clipboard,
the look (dark or light, accent color), power, and where the user's folders and screenshots are.

Linux desktops differ, so each answer comes from the freedesktop.org standard every desktop follows where there is
one (XDG folders and autostart, xdg-open, the FileManager1 D-Bus interface, notify-send), else from what GNOME and KDE
record, else a plain default. The clipboard is Qt's, in the app's process (its tray runs Qt), which holds what's
copied for as long as Dowse runs, as a Linux clipboard needs.
"""

import configparser
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import quote

from .brand import APP_PNG


def _run(command: list[str], timeout: float = 5) -> str:
    """A command's output, or "" when it isn't installed or fails."""
    if shutil.which(command[0]) is None:
        return ""
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _start(command: list[str]) -> bool:
    if shutil.which(command[0]) is None:
        return False
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return True


def startup_error(message: str) -> None:
    """A failure before the app (and its log) can open, said in a dialog where there's a tool for it."""
    title = "Dowse couldn't start"
    if not (_run(["zenity", "--error", f"--title={title}", f"--text={message}"], timeout=600) or
            _run(["kdialog", "--error", message, "--title", title], timeout=600)):
        print(f"{title}: {message}", flush=True)


def notify(title: str, message: str) -> bool:
    """A desktop notification (False when there's no notify-send: the tray says it instead)."""
    return _start(["notify-send", "--app-name=Dowse", f"--icon={APP_PNG}", title, message])


# Starting at login: XDG autostart, a .desktop file of the user's

def _autostart_file() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config / "autostart" / "dowse.desktop"


def starts_at_login() -> bool:
    return _autostart_file().exists()


def _quoted(argument: str) -> str:
    """An argument as a .desktop file's Exec line takes it."""
    if re.fullmatch(r"[\w@%+=:,./-]+", argument):
        return argument
    return '"' + re.sub(r'(["`$\\])', r"\\\1", argument) + '"'


def set_starts_at_login(enabled: bool, program: str, arguments: list[str]) -> None:
    entry = _autostart_file()
    if not enabled:
        entry.unlink(missing_ok=True)
        return
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(_desktop_entry([program, *arguments, "--background"]) + "X-GNOME-Autostart-enabled=true\n",
                     encoding="utf-8")


def _data() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _icon() -> Path:
    """Dowse's icon where the desktop can always find it: the copy in the user's icons once there (an AppImage's own
    files are only there while it runs), else the package's."""
    installed = _data() / "icons" / "hicolor" / "256x256" / "apps" / "dowse.png"
    return installed if installed.exists() else APP_PNG


def _desktop_entry(command: list[str]) -> str:
    return ("[Desktop Entry]\nType=Application\nName=Dowse\nComment=Find files by what's in them\n"
            f"Exec={' '.join(_quoted(a) for a in command)}\nIcon={_icon()}\nTerminal=false\n"
            "Categories=Utility;FileTools;\nKeywords=search;find;files;photos;documents;\nStartupWMClass=dowse\n")


def integrate() -> None:
    """Run from an AppImage (`$APPIMAGE`, the file itself; the app's own files are mounted somewhere new each time):
    Dowse in the apps menu, which an AppImage doesn't do for itself and where Dowse is found again when the desktop
    shows no tray icons; its icon beside it; and opening at login following the file if it was moved."""
    image = os.environ.get("APPIMAGE")
    if not image or not os.path.isfile(image):
        return
    icon = _data() / "icons" / "hicolor" / "256x256" / "apps" / "dowse.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(APP_PNG, icon)
    entry = _data() / "applications" / "dowse.desktop"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(_desktop_entry([image]), encoding="utf-8")
    if starts_at_login():
        set_starts_at_login(True, image, [])


# Opening a file where it matched

def default_app(path: str) -> str | None:
    """The desktop file of the app that opens `path` on a double-click ("org.gnome.Evince.desktop"), or None."""
    mime = _run(["xdg-mime", "query", "filetype", path])
    if not mime:
        return None
    return _run(["xdg-mime", "query", "default", mime]) or None


def open_command(path: str, kind: str, loc: float | None, app: str | None) -> list[str] | None:
    """The command that opens the file where it matched (a PDF's page, a recording's moment) with `app` (its desktop
    file's name), or None when that app can't be told where (then the file just opens)."""
    if app is None or loc is None:
        return None
    name = app.lower().removesuffix(".desktop")
    if kind == "pdf":
        page = str(int(loc))
        if "evince" in name or "papers" in name:   # GNOME's viewer, and its successor
            return ["evince" if shutil.which("evince") else "papers", "--page-index", page, path]
        if "okular" in name:
            return ["okular", "--page", page, path]
        if any(browser in name for browser in ("firefox", "chrome", "chromium", "brave", "edge")):
            program = next((p for p in ("firefox", "google-chrome", "chromium", "brave-browser", "microsoft-edge")
                            if p.split("-")[0] in name and shutil.which(p)), None)
            return [program, Path(path).as_uri() + f"#page={page}"] if program else None
        return None
    if kind in ("audio", "video"):
        seconds = max(int(loc), 0)
        if "vlc" in name:
            return ["vlc", f"--start-time={seconds}", path]
        if "mpv" in name or "celluloid" in name:
            return ["mpv", f"--start={seconds}", path] if shutil.which("mpv") else None
    return None


def open_file(path: str, kind: str, loc: float | None = None) -> None:
    command = open_command(path, kind, loc, default_app(path))
    if not (command and _start(command)):
        _start(["xdg-open", path])


def open_folder(path: str) -> None:
    _start(["xdg-open", path])


def reveal(path: str) -> None:
    """The file manager, with the file selected (FileManager1: Files, Dolphin, Nemo, Thunar…), else its folder."""
    uri = "file://" + quote(path)
    if _run(["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1", "--object-path",
             "/org/freedesktop/FileManager1", "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""]):
        return
    if _run(["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
             "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems", f"array:string:{uri}",
             "string:"]):
        return
    open_folder(os.path.dirname(path))


# The clipboard

def _on_qt(apply) -> None:
    """`apply` on the Qt thread of this process (the tray's), waiting for it; without Qt, wl-copy or xclip instead."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        raise RuntimeError("no Qt application in this process")
    done = threading.Event()

    def run():
        try:
            apply()
        finally:
            done.set()

    QTimer.singleShot(0, app, run)
    done.wait(5)


def _qt_running() -> bool:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return False
    return QApplication.instance() is not None


def copy_path(path: str) -> None:
    if _qt_running():
        from PySide6.QtWidgets import QApplication
        return _on_qt(lambda: QApplication.clipboard().setText(path))
    data = path.encode()
    for command in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            subprocess.run(command, input=data, timeout=5, check=False)
            return


def copy_file(path: str) -> None:
    """The file itself, as file managers copy it: paste it into a folder, a chat, an email."""
    uri = Path(path).as_uri()
    if not _qt_running():
        return copy_path(path)
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtWidgets import QApplication

    def apply():
        data = QMimeData()
        data.setUrls([QUrl(uri)])
        data.setData("x-special/gnome-copied-files", f"copy\n{uri}".encode())   # Files, Nemo, Caja
        data.setText(path)
        QApplication.clipboard().setMimeData(data)

    _on_qt(apply)


# The look

_GNOME_ACCENTS = {"blue": "#3584e4", "teal": "#2190a4", "green": "#3a944a", "yellow": "#c88800", "orange": "#ed5b00",
                  "red": "#e62d42", "pink": "#d56199", "purple": "#9141ac", "slate": "#6f8396"}


def theme() -> dict:
    """{"dark": bool, "accent": "#rrggbb"}: GNOME's color scheme and accent, else KDE's colors, else light and blue."""
    dark, accent = False, _GNOME_ACCENTS["blue"]
    scheme = _run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"]).strip("'")
    if scheme:
        dark = scheme == "prefer-dark"
        accent = _GNOME_ACCENTS.get(_run(["gsettings", "get", "org.gnome.desktop.interface", "accent-color"])
                                    .strip("'"), accent)
        return {"dark": dark, "accent": accent}
    kde = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        kde.read(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kdeglobals")
        window = kde.get("Colors:Window", "BackgroundNormal", fallback="")
        if window:
            r, g, b = (int(c) for c in window.split(",")[:3])
            dark = (0.299 * r + 0.587 * g + 0.114 * b) < 128
        if colour := kde.get("General", "AccentColor", fallback=""):
            accent = "#{:02x}{:02x}{:02x}".format(*(int(c) for c in colour.split(",")[:3]))
    except (configparser.Error, ValueError, OSError):
        pass
    return {"dark": dark, "accent": accent}


# Power and folders

def power(supplies: Path = Path("/sys/class/power_supply")) -> tuple[bool, bool]:
    """(on battery, power saver on): a battery and no mains connected; the power profile (power-profiles-daemon) or
    the firmware's platform profile set to save power."""
    def read(path: Path) -> str:
        try:
            return path.read_text().strip()
        except OSError:
            return ""

    kinds = {supply: read(supply / "type") for supply in supplies.glob("*")}
    mains = [s for s, kind in kinds.items() if kind == "Mains"]
    battery = any(kind == "Battery" for kind in kinds.values()) and bool(mains) and \
        not any(read(s / "online") == "1" for s in mains)
    profile = _run(["powerprofilesctl", "get"]) or read(Path("/sys/firmware/acpi/platform_profile"))
    return battery, profile in ("power-saver", "low-power", "quiet")


_XDG_NAMES = {"Desktop": "DESKTOP", "Documents": "DOCUMENTS", "Downloads": "DOWNLOAD", "Pictures": "PICTURES",
              "Videos": "VIDEOS", "Music": "MUSIC"}


def known_folder(name: str) -> str:
    """The user's Desktop, Documents, Downloads, Pictures, Videos or Music, where XDG's user-dirs.dirs puts them
    (translated names: ~/Bilder, ~/Imágenes)."""
    home = str(Path.home())
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path(home) / ".config") / "user-dirs.dirs"
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == f"XDG_{_XDG_NAMES[name]}_DIR":
                folder = value.strip().strip('"').replace("$HOME", home)
                if folder and os.path.normpath(folder) != os.path.normpath(home):   # "$HOME/": none set
                    return os.path.normpath(folder)
    except OSError:
        pass
    return os.path.join(home, name)


def screenshots_folder() -> str:
    """Where screenshots are saved: GNOME's Pictures/Screenshots, KDE Spectacle's chosen folder, else Pictures."""
    spectacle = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        spectacle.read(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "spectaclerc")
        chosen = spectacle.get("ImageSave", "imageSaveLocation", fallback="")
        if chosen.startswith("file://"):
            chosen = chosen[len("file://"):]
        if chosen and os.path.isdir(chosen):
            return chosen
    except configparser.Error:
        pass
    pictures = known_folder("Pictures")
    gnome = os.path.join(pictures, "Screenshots")
    return gnome if os.path.isdir(gnome) else pictures
