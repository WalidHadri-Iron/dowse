"""The app's presence in Windows: the tray icon, the global shortcut, and its one window.

The window lives in a process of its own (`windows`), started on demand and gone after ten minutes hidden:
WebView2 holds ~240 MB for as long as a window exists, and Dowse at rest is the tray icon. That process
is tied to this one (a kill-on-close job), so it never outlives the app.

- The shortcut is registered on a thread of its own (RegisterHotKey), which also re-registers it when it changes.
- The tray menu shows how indexing is going and leads everywhere; left-click opens search.
"""

import ctypes
import ctypes.wintypes as w
import itertools
import json
import logging
import subprocess
import sys
import threading

from .. import runtime
from . import winapi
from .brand import ICON
from .indexing import Status

log = logging.getLogger("dowse.native")

WM_HOTKEY, WM_APP = 0x0312, 0x8000


class Hotkey:
    """The global shortcut, on a thread with its own message queue."""

    def __init__(self, text: str, pressed):
        self.text, self.pressed = text, pressed
        self.pending = text
        self._thread_id = 0
        self._result = threading.Event()
        self._ok = False
        threading.Thread(target=self._run, name="hotkey", daemon=True).start()

    def _run(self) -> None:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        self._ok = self._register(self.text)
        self._result.set()
        message = w.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY:
                threading.Thread(target=self.pressed, daemon=True).start()   # never block the message loop
            elif message.message == WM_APP + 1:   # a new shortcut was asked for: `change`
                user32.UnregisterHotKey(None, 1)
                self._ok = self._register(self.pending)
                if self._ok:
                    self.text = self.pending
                else:
                    self._register(self.text)      # keep the old one
                self._result.set()

    def _register(self, text: str) -> bool:
        modifiers, key = winapi.parse_hotkey(text)
        return bool(ctypes.windll.user32.RegisterHotKey(None, 1, modifiers | 0x4000, key))   # MOD_NOREPEAT

    def registered(self) -> bool:
        self._result.wait(2)
        return self._ok

    def change(self, text: str) -> bool:
        self.pending = text
        self._result.clear()
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_APP + 1, 0, 0)
        self._result.wait(2)
        return self._ok


class WindowHost:
    """The windows' process, started on demand: commands in on its stdin, answers and events out on its stdout."""

    def __init__(self, url: str, token: str, dark: bool, command: list[str] | None = None):
        self.url, self.token, self.dark = url, token, dark
        self.command = command or [sys.executable, "-m", "dowse.desktop.windows"]   # (a stand-in, in the tests)
        self._process: subprocess.Popen | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._waiting: dict[int, tuple[threading.Event, list]] = {}

    def _ensure(self) -> subprocess.Popen:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._process
            self._ready.clear()
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._process = subprocess.Popen([*self.command, self.url, self.token],
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                             encoding="utf-8", creationflags=flags, bufsize=1)
            runtime._die_with_us(self._process)
            threading.Thread(target=self._read, args=(self._process,), name="windows", daemon=True).start()
            process = self._process
            if not self._ready.wait(15):
                log.error("the windows didn't start")
            else:
                self._write(process, {"op": "theme", "dark": self.dark})
        return process

    def _read(self, process: subprocess.Popen) -> None:
        for line in process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("event") == "ready":
                self._ready.set()
            elif message.get("event") == "idle":
                log.info("windows closed for now (idle)")
            elif "id" in message and message["id"] in self._waiting:
                done, box = self._waiting.pop(message["id"])
                box.append(message.get("result"))
                done.set()

    def _write(self, process: subprocess.Popen, command: dict) -> None:
        try:
            process.stdin.write(json.dumps(command) + "\n")
            process.stdin.flush()
        except (OSError, ValueError):
            log.warning("the windows went away")

    def call(self, op: str, wait: float = 0, **fields):
        """Sends a command; with `wait`, the answer (None if it doesn't come in that many seconds)."""
        process = self._ensure()
        command = {"id": next(self._ids), "op": op, **fields}
        done, box = threading.Event(), []
        if wait:
            self._waiting[command["id"]] = (done, box)
        self._write(process, command)
        if wait and done.wait(wait):
            return box[0] if box else None
        return None

    def notify_theme(self, dark: bool) -> None:
        self.dark = dark
        if self.running():
            self._write(self._process, {"op": "theme", "dark": dark})

    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
        if self.running():
            self._write(self._process, {"op": "quit"})
            try:
                self._process.wait(5)
            except subprocess.TimeoutExpired:
                self._process.kill()


class Native:
    def __init__(self, app):
        self.app = app
        self.windows = WindowHost(app.server.url, app.server.token, bool(app._theme["dark"]))
        self.hotkey: Hotkey | None = None
        self.tray = None
        self._paused = False
        self._status = Status("starting", "Starting…")
        self._quit = threading.Event()

    def run(self, show: str | None) -> None:
        """Until Quit (on the main thread)."""
        self.hotkey = Hotkey(self.app.settings["hotkey"], self.toggle_popup)
        self._tray()
        if not self.hotkey.registered():
            self.notify("Dowse", f"{self.app.settings['hotkey']} is taken by another app. Search from the tray "
                                 "icon, or choose another shortcut in Settings.")
        if show == "popup":
            self.show_popup()
        elif show:
            self.show_main(show)
        self._quit.wait()

    def _tray(self) -> None:
        import pystray
        from PIL import Image

        menu = pystray.Menu(
            pystray.MenuItem(lambda _: self._status.text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quick search", lambda: self.show_popup(), default=True),
            pystray.MenuItem("Workspace", lambda: self.show_main("/search")),
            pystray.MenuItem("Library", lambda: self.show_main("/library")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pause indexing", lambda: self.app.change_settings({"paused": not self._paused}),
                             checked=lambda _: self._paused),
            pystray.MenuItem("Settings", lambda: self.show_main("/settings")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Dowse", lambda: self.quit()),
        )
        self.tray = pystray.Icon("Dowse", Image.open(ICON), "Dowse", menu)
        threading.Thread(target=self.tray.run, name="tray", daemon=True).start()

    # Windows

    def show_popup(self, route: str | None = None, wait: float = 0):
        self.app.engine.warm()
        return self.windows.call("popup", route=route, wait=wait)

    def hide_popup(self) -> None:
        if self.windows.running():
            self.windows.call("hidePopup")

    def toggle_popup(self) -> None:
        self.app.engine.warm()
        self.windows.call("togglePopup")

    def show_main(self, route: str = "/search", wait: float = 0):
        if route.startswith("/search"):
            self.app.engine.warm()
        return self.windows.call("main", route=route, wait=wait)

    def pick(self, what: str) -> list[str]:
        return self.windows.call("pick", wait=600, what=what) or []

    def theme(self, look: dict) -> None:
        self.windows.notify_theme(bool(look["dark"]))

    def set_hotkey(self, text: str) -> bool:
        return self.hotkey.change(text) if self.hotkey else False

    # The tray

    def status(self, status: Status) -> None:
        self._status = status
        if self.tray is not None:
            self.tray.title = f"Dowse\n{status.text}"[:127]
            self.tray.update_menu()

    def paused(self, paused: bool) -> None:
        self._paused = paused
        if self.tray is not None:
            self.tray.update_menu()

    def notify(self, title: str, message: str) -> None:
        if self.tray is not None:
            try:
                self.tray.notify(message, title)
            except Exception:
                log.warning("notification: %s", title)

    def quit(self) -> None:
        self.windows.stop()
        if self.tray is not None:
            self.tray.stop()
        self._quit.set()
