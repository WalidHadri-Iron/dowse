"""Dowse in Explorer: a "Dowse" submenu when files or folders are right-clicked (Add to Dowse, Remove from Dowse, for
a file Find similar, and for a folder Search in this folder), "Search in this folder with Dowse" inside a folder, and
Dowse in Send to. For the current user only (HKCU and the user's own Send to folder): no admin rights, nothing
system-wide, and it all goes when it's turned off.

On Windows 11 these show under "Show more options" (Shift+F10): the first menu only lists apps that are packaged
and signed, which is the installer's job. Explorer runs a menu command once per selected file (up to 100 here, with
`MultiSelectModel` "Player"); Send to passes them all at once. Either way `launch` gathers them into one change.
"""

import ctypes
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from . import winapi
from .brand import ICON

log = logging.getLogger("dowse.shell")

CLASSES = r"Software\Classes"

# where → [(key, label, argument)]; keys sort the submenu
MENUS = {
    "*": [("1add", "Add to Dowse", "--add"), ("2similar", "Find similar", "--like"),
          ("3remove", "Remove from Dowse", "--remove")],
    "Directory": [("1add", "Add to Dowse", "--add"), ("2search", "Search in this folder", "--search"),
                  ("3remove", "Remove from Dowse", "--remove")],
}
# Right-click inside a folder (its background): one verb, no submenu. Explorer gives the folder as %V there.
BACKGROUND = (r"Directory\Background\shell\DowseSearch", "Search in this folder with Dowse", "--search")


def send_to_folder() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "SendTo"


def _command(argument: str, target: str = "%1") -> str:
    return f'{winapi.launch_command()} {argument} "{target}"'


def wanted() -> dict[str, dict[str, str]]:
    """Every key under the classes root, and its values ("" is a key's default value), as they should be."""
    out = {}
    for where, items in MENUS.items():
        root = rf"{where}\shell\Dowse"
        out[root] = {"MUIVerb": "Dowse", "Icon": str(ICON), "SubCommands": "", "MultiSelectModel": "Player"}
        for key, label, argument in items:
            out[rf"{root}\shell\{key}"] = {"": label, "MultiSelectModel": "Player"}
            out[rf"{root}\shell\{key}\command"] = {"": _command(argument)}
    key, label, argument = BACKGROUND
    out[key] = {"MUIVerb": label, "Icon": str(ICON)}
    out[rf"{key}\command"] = {"": _command(argument, "%V")}
    return out


def registered(classes: str = CLASSES) -> bool:
    """Is the menu there, as it should be (the same program: a moved Python or app folder makes it stale)?"""
    if not winapi.WINDOWS:
        return False
    import winreg

    for key, values in wanted().items():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{classes}\{key}") as handle:
                for name, value in values.items():
                    if winreg.QueryValueEx(handle, name)[0] != value:
                        return False
        except OSError:
            return False
    return True


def register(classes: str = CLASSES, send_to: Path | None = None) -> None:
    if not winapi.WINDOWS:
        return
    import winreg

    _remove_keys(classes)   # nothing left over from an older menu
    for key, values in wanted().items():
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"{classes}\{key}", 0, winreg.KEY_SET_VALUE) as handle:
            for name, value in values.items():
                winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)
    _shortcut(send_to or send_to_folder())
    _changed()
    log.info("in Explorer's menu")


def unregister(classes: str = CLASSES, send_to: Path | None = None) -> None:
    if not winapi.WINDOWS:
        return
    _remove_keys(classes)
    (send_to or send_to_folder()).joinpath("Dowse.lnk").unlink(missing_ok=True)
    _changed()
    log.info("out of Explorer's menu")


def keep_registered() -> None:
    """At start: puts the menu back if it's missing or stale, in the background (making the Send to shortcut takes
    a moment)."""
    if winapi.WINDOWS and not (registered() and send_to_folder().joinpath("Dowse.lnk").exists()):
        threading.Thread(target=register, daemon=True, name="explorer menu").start()


def _remove_keys(classes: str) -> None:
    for where in MENUS:
        _delete_tree(rf"{classes}\{where}\shell\Dowse")
    _delete_tree(rf"{classes}\{BACKGROUND[0]}")


def _delete_tree(key: str) -> None:
    """Deletes `key` (under HKCU) and everything under it; nothing if it isn't there."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            children = []
            while True:
                try:
                    children.append(winreg.EnumKey(handle, len(children)))
                except OSError:
                    break
    except OSError:
        return
    for child in children:
        _delete_tree(rf"{key}\{child}")
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)


def _shortcut(folder: Path) -> None:
    """Send to › Dowse: a shortcut to the launcher with --add (Send to appends the files)."""
    program, arguments = winapi.launcher()
    target = folder / "Dowse.lnk"
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:DOWSE_LNK); "
              "$s.TargetPath = $env:DOWSE_PROGRAM; $s.Arguments = $env:DOWSE_ARGUMENTS; "
              "$s.IconLocation = $env:DOWSE_ICON; $s.Description = 'Add to Dowse'; $s.Save()")
    folder.mkdir(parents=True, exist_ok=True)
    environment = os.environ | {"DOWSE_LNK": str(target), "DOWSE_PROGRAM": program, "DOWSE_ICON": f"{ICON},0",
                                "DOWSE_ARGUMENTS": subprocess.list2cmdline([*arguments, "--add"])}
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=environment, capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        log.warning("Send to shortcut: %s", result.stderr.strip()[:300])


def _changed() -> None:
    """Tells Explorer its menus changed (it caches them)."""
    SHCNE_ASSOCCHANGED = 0x08000000   # noqa: N806 (Windows' names)
    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, 0, None, None)


if __name__ == "__main__":   # python -m dowse.desktop.shell [--remove]: for an installer, or by hand
    (unregister if "--remove" in sys.argv else register)()
