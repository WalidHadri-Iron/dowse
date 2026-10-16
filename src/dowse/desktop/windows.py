"""One app window, in a process of its own: quick search and the workspace in the same WebView2.

The app (tray, shortcut, engine, server) starts this process when a window is wanted, and it exits after `IDLE`
seconds with no window shown: WebView2 holds ~240 MB for as long as any window exists, and a tray app at rest
shouldn't. Commands come in on stdin, one JSON object a line ({"id", "op", ...}); answers go out on stdout
({"id", "result"}), and so do events ({"event": "..."}).

    python -m dowse.desktop.windows <server url> <token>
"""

import contextlib
import ctypes
import ctypes.wintypes as w
import json
import os
import sys
import threading
import time

import webview

from .. import paths
from . import winapi
from .brand import APP_ID, ICON

POPUP_SIZE = (860, 600)   # logical pixels, including the native window frame
POPUP_MIN = (640, 440)
MAIN_SIZE = (1280, 820)
MAIN_MIN = (980, 620)
IDLE = float(os.environ.get("DOWSE_WINDOWS_IDLE", 600))   # seconds with no window shown before this process ends


def _cursor_screen() -> tuple[float, float, float, float]:
    """The work area of the screen the mouse is on, in scaled pixels."""
    point = w.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    monitor = ctypes.windll.user32.MonitorFromPoint(point, 2)

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD)]

    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    scale = ctypes.windll.user32.GetDpiForSystem() / 96
    work = info.rcWork
    return work.left / scale, work.top / scale, work.right / scale, work.bottom / scale


class Windows:
    def __init__(self, url: str, token: str):
        # This is a separate pythonw process: the tray process's identity is not inherited.
        winapi.set_app_id(APP_ID)
        self.url, self.token = url, token
        self.dark = False
        self.mode = "compact"
        self.lock = threading.RLock()
        self.presented = False
        self.visible = False   # pywebview's `hidden` is a creation option, not live visibility
        self.quitting = False
        self.picking = False
        self.bounds: dict[str, tuple[int, int, int, int]] = {}
        self.maximized: dict[str, bool] = {}
        self.last_shown = time.monotonic()
        self.write_lock = threading.Lock()
        self.window = webview.create_window("Dowse — Quick search", self.page("/popup"), width=POPUP_SIZE[0],
                                            height=POPUP_SIZE[1], min_size=POPUP_MIN, hidden=True,
                                            on_top=False, background_color="#e4e9e7")
        self.window.events.closing += self.closing
        self.window.events.shown += self.title_bar

    def page(self, route: str) -> str:
        return f"{self.url}?t={self.token}#{route}"

    # The protocol

    def send(self, message: dict) -> None:
        with self.write_lock:
            sys.stdout.write(json.dumps(message) + "\n")
            sys.stdout.flush()

    def listen(self) -> None:
        for line in sys.stdin:
            try:
                command = json.loads(line)
            except ValueError:
                continue
            try:
                result = self.do(command)
            except Exception as error:   # a window that went away mid-command: said, and the process goes on
                result = {"error": f"{type(error).__name__}: {error}"}
            if "id" in command:
                self.send({"id": command["id"], "result": result})
        self.quit()   # the app went away

    def do(self, command: dict):
        op = command.get("op")
        if op == "popup":
            return self.show_popup(command.get("route"))
        if op == "togglePopup":
            return self.toggle()
        if op == "hidePopup":
            return self.hide_popup()
        if op == "main":
            return self.show_main(command.get("route", "/search"))
        if op == "pick":
            return self.pick(command.get("what", "files"))
        if op == "theme":
            self.dark = bool(command.get("dark"))
            self.title_bar()
            return True
        if op == "quit":
            self.quit()
        return None

    # One window, two modes. Routes change within the existing page: its search state stays alive.

    def _scale(self) -> float:
        user32 = ctypes.windll.user32
        user32.GetDpiForWindow.argtypes = [w.HWND]
        user32.GetDpiForWindow.restype = w.UINT
        return (user32.GetDpiForWindow(int(self.window.native.Handle.ToInt64())) or 96) / 96

    def _minimum(self, size: tuple[int, int]) -> None:
        """pywebview sets the minimum at creation; WinForms can change it when the window's mode changes."""
        if not self.window.events.shown.wait(20):
            raise RuntimeError("the window didn't initialize")
        from System.Drawing import Size

        def apply():
            native = self.window.native
            scale = self._scale()
            native.MinimumSize = Size(int(size[0] * scale), int(size[1] * scale))

        self._on_ui(apply)

    def _on_ui(self, apply) -> None:
        from System import Action

        self.window.native.Invoke(Action(apply))

    def _topmost(self, on_top: bool) -> None:
        # pywebview's Windows setter assigns Form.TopMost directly. From the command thread that can block
        # on WinForms while holding Python's GIL, deadlocking the UI after a mouse click. Marshal the setter.
        self._on_ui(lambda: setattr(self.window, "on_top", on_top))

    def _remember(self) -> None:
        if self.presented:
            # pywebview resizes through SetWindowPos; WinForms' RestoreBounds stays stale while normal.
            # Bounds is current in normal state; RestoreBounds is the saved rectangle when minimized/maximized.
            native = self.window.native
            scale = self._scale()
            state = native.WindowState.ToString()
            rect = native.Bounds if state == "Normal" else native.RestoreBounds
            self.bounds[self.mode] = tuple(round(n / scale) for n in (rect.X, rect.Y, rect.Width, rect.Height))
            self.maximized[self.mode] = state == "Maximized"

    def _place(self, mode: str) -> None:
        left, top, right, bottom = _cursor_screen()
        default = POPUP_SIZE if mode == "compact" else MAIN_SIZE
        width, height = min(default[0], int(right - left)), min(default[1], int(bottom - top))
        x = int(left + (right - left - width) / 2)
        y = int(top + (bottom - top - height) * (0.32 if mode == "compact" else 0.5))
        x, y, width, height = self.bounds.get(mode, (x, y, width, height))
        width, height = min(width, int(right - left)), min(height, int(bottom - top))
        x = max(int(left), min(x, int(right) - width))
        y = max(int(top), min(y, int(bottom) - height))
        minimum = POPUP_MIN if mode == "compact" else MAIN_MIN
        self._minimum((min(minimum[0], int(right - left)), min(minimum[1], int(bottom - top))))
        self.window.restore()
        self.window.resize(width, height)
        self.window.move(x, y)

    def show(self, mode: str, route: str | None = None) -> bool:
        with self.lock:
            switching = mode != self.mode or not self.presented
            if switching:
                self._remember()
            self.mode = mode
            if switching:
                self._place(mode)
            self.window.set_title("Dowse — Quick search" if mode == "compact" else "Dowse")
            # Quick search stays open on blur, but must yield to other apps just like the workspace.
            self._topmost(False)
            # An explicit route comes from Explorer or a mode control; a plain switch keeps the live search.
            target = route or ("/popup" if mode == "compact" else "/search")
            self.window.evaluate_js(f"location.hash = {json.dumps(target)}")
            if not self.visible:
                self.window.restore()
            self.window.show()
            if (switching or not self.visible) and self.maximized.get(mode):
                self.window.maximize()
            self.visible = True
            self.presented = True
            self.window.evaluate_js("window.dispatchEvent(new Event('dowse:shown'))")
            self.last_shown = time.monotonic()
        return True

    def show_popup(self, route: str | None = None) -> bool:
        return self.show("compact", route)

    def toggle(self) -> bool:
        """The shortcut brings the existing mode forward; pressing it while Dowse is focused hides it."""
        if self.visible and self.focused():
            return self.hide()
        if not self.presented:
            return self.show_popup()
        if not self.visible or self.window.native.WindowState.ToString() == "Minimized":
            self.window.restore()
            if self.maximized.get(self.mode):
                self.window.maximize()
        self._topmost(False)
        self.window.show()
        self.visible = True
        self.window.evaluate_js("window.dispatchEvent(new Event('dowse:shown'))")
        self.last_shown = time.monotonic()
        return True

    def focused(self) -> bool:
        if not self.window.events.shown.wait(20):
            return False
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = w.HWND
        return user32.GetForegroundWindow() == int(self.window.native.Handle.ToInt64())

    def hide(self) -> bool:
        with self.lock:
            self._remember()
            self.window.hide()
            self.visible = False
            self.last_shown = time.monotonic()
        return True

    def hide_popup(self) -> bool:
        # An explicit compact dismissal must never hide a workspace or its native file dialog.
        with self.lock:
            return self.hide() if self.mode == "compact" and not self.picking else False

    def show_main(self, route: str) -> bool:
        return self.show("full", route)

    def closing(self) -> bool:
        if self.quitting:
            return True
        # WinForms runs this callback on its UI thread; hide there, without waiting on a cross-thread invoke.
        self._remember()
        self.window.native.Hide()
        self.visible = False
        self.last_shown = time.monotonic()
        return False

    def title_bar(self) -> None:
        with contextlib.suppress(Exception):
            winapi.dark_title_bar(int(self.window.native.Handle.ToInt64()), self.dark)

    def pick(self, what: str) -> list[str]:
        self.picking = True
        try:
            if what == "folder":
                chosen = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            else:
                chosen = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
            return list(chosen or [])
        finally:
            self.picking = False

    def idle(self) -> None:
        """Ends the process after `IDLE` seconds with nothing shown: its memory goes back to Windows."""
        while True:
            time.sleep(min(15, IDLE / 2))
            showing = self.visible
            if showing:
                self.last_shown = time.monotonic()
            elif time.monotonic() - self.last_shown >= IDLE:
                self.send({"event": "idle"})
                self.quit()
                return

    def quit(self) -> None:
        self.quitting = True
        for window in list(webview.windows):
            with contextlib.suppress(Exception):
                window.destroy()


def main() -> None:
    url, token = sys.argv[1], sys.argv[2]
    windows = Windows(url, token)

    def started() -> None:
        threading.Thread(target=windows.listen, name="commands", daemon=True).start()
        threading.Thread(target=windows.idle, name="idle", daemon=True).start()
        windows.send({"event": "ready"})

    webview.start(started, gui="edgechromium", icon=str(ICON), private_mode=False,
                  storage_path=str(paths.home() / "WebView"))


if __name__ == "__main__":
    main()
