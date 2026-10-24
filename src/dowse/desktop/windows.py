"""One app window, in a process of its own: quick search and the workspace in the same web view (WebView2 on
Windows, WKWebView on a Mac, Qt WebEngine on Linux).

The app (tray, shortcut, engine, server) starts this process when a window is wanted, and it exits after `IDLE`
seconds with no window shown: WebView2 holds ~240 MB for as long as any window exists, and a tray app at rest
shouldn't. Commands come in on stdin, one JSON object a line ({"id", "op", ...}); answers go out on stdout
({"id", "result"}), and so do events ({"event": "..."}).

    python -m dowse.desktop.windows <server url> <token>
"""

import contextlib
import ctypes
import json
import os
import sys
import threading
import time

if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_API", "pyside6")   # pywebview's Qt backend, through QtPy: PySide6 (LGPL)

import webview

from .. import paths
from . import winapi
from .brand import APP_ID, APP_PNG, ICON

POPUP_SIZE = (860, 600)   # logical pixels, including the native window frame
POPUP_MIN = (640, 440)
MAIN_SIZE = (1280, 820)
MAIN_MIN = (980, 620)
IDLE = float(os.environ.get("DOWSE_WINDOWS_IDLE", 600))   # seconds with no window shown before this process ends


class _WindowsHost:
    """What the window needs from Windows: WinForms behind pywebview, and user32."""

    GUI = "edgechromium"

    def __init__(self):
        import ctypes.wintypes as w
        self.w = w
        self.user32 = ctypes.windll.user32

    def app_id(self) -> None:
        # This is a separate pythonw process: the tray process's identity is not inherited.
        winapi.set_app_id(APP_ID)

    def cursor_screen(self) -> tuple[float, float, float, float]:
        """The work area of the screen the mouse is on, in scaled pixels."""
        w = self.w
        point = w.POINT()
        self.user32.GetCursorPos(ctypes.byref(point))
        monitor = self.user32.MonitorFromPoint(point, 2)

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD)]

        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        self.user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        scale = self.user32.GetDpiForSystem() / 96
        work = info.rcWork
        return work.left / scale, work.top / scale, work.right / scale, work.bottom / scale

    def _handle(self, window) -> int:
        return int(window.native.Handle.ToInt64())

    def scale(self, window) -> float:
        self.user32.GetDpiForWindow.argtypes = [self.w.HWND]
        self.user32.GetDpiForWindow.restype = self.w.UINT
        return (self.user32.GetDpiForWindow(self._handle(window)) or 96) / 96

    def on_ui(self, window, apply) -> None:
        from System import Action

        window.native.Invoke(Action(apply))

    def minimum(self, window, size: tuple[int, int]) -> None:
        """pywebview sets the minimum at creation; WinForms can change it when the window's mode changes."""
        from System.Drawing import Size

        def apply():
            scale = self.scale(window)
            window.native.MinimumSize = Size(int(size[0] * scale), int(size[1] * scale))

        self.on_ui(window, apply)

    def topmost(self, window, on_top: bool) -> None:
        # pywebview's Windows setter assigns Form.TopMost directly. From the command thread that can block
        # on WinForms while holding Python's GIL, deadlocking the UI after a mouse click. Marshal the setter.
        self.on_ui(window, lambda: setattr(window, "on_top", on_top))

    def bounds(self, window) -> tuple[tuple[int, int, int, int], bool]:
        """The window's normal rectangle (logical pixels), and whether it's maximized."""
        # pywebview resizes through SetWindowPos; WinForms' RestoreBounds stays stale while normal.
        # Bounds is current in normal state; RestoreBounds is the saved rectangle when minimized/maximized.
        native = window.native
        scale = self.scale(window)
        state = native.WindowState.ToString()
        rect = native.Bounds if state == "Normal" else native.RestoreBounds
        return tuple(round(n / scale) for n in (rect.X, rect.Y, rect.Width, rect.Height)), state == "Maximized"

    def minimized(self, window) -> bool:
        return window.native.WindowState.ToString() == "Minimized"

    def focused(self, window) -> bool:
        self.user32.GetForegroundWindow.restype = self.w.HWND
        return self.user32.GetForegroundWindow() == self._handle(window)

    def hide_now(self, window) -> None:
        # WinForms runs the closing callback on its UI thread; hide there, without waiting on a cross-thread invoke.
        window.native.Hide()

    def bring_forward(self, window) -> None:
        pass   # showing the window brings it forward

    def title_bar(self, window, dark: bool) -> None:
        winapi.dark_title_bar(self._handle(window), dark)


class _MacHost:
    """What the window needs from macOS: AppKit (PyObjC) behind pywebview's Cocoa backend. Sizes are points, the
    origin the top left of the main screen (as pywebview places windows); AppKit's origin is the bottom left."""

    GUI = "cocoa"

    def app_id(self) -> None:
        pass   # the app's identity is its bundle

    @staticmethod
    def _main_height() -> float:
        from AppKit import NSScreen

        return NSScreen.screens()[0].frame().size.height

    def cursor_screen(self) -> tuple[float, float, float, float]:
        """The visible area (without the menu bar and the Dock) of the screen the mouse is on."""
        from AppKit import NSEvent, NSPointInRect, NSScreen

        mouse = NSEvent.mouseLocation()
        screens = NSScreen.screens()
        screen = next((s for s in screens if NSPointInRect(mouse, s.frame())), screens[0])
        area = screen.visibleFrame()
        top = self._main_height() - (area.origin.y + area.size.height)
        return area.origin.x, top, area.origin.x + area.size.width, top + area.size.height

    def scale(self, window) -> float:
        return 1.0   # pywebview works in points on a Mac

    def on_ui(self, window, apply) -> None:
        from PyObjCTools import AppHelper

        done = threading.Event()

        def run():
            try:
                apply()
            finally:
                done.set()

        AppHelper.callAfter(run)
        done.wait(5)

    def minimum(self, window, size: tuple[int, int]) -> None:
        from Foundation import NSMakeSize

        self.on_ui(window, lambda: window.native.setContentMinSize_(NSMakeSize(*size)))

    def topmost(self, window, on_top: bool) -> None:
        self.on_ui(window, lambda: setattr(window, "on_top", on_top))

    def bounds(self, window) -> tuple[tuple[int, int, int, int], bool]:
        native = window.native
        frame = native.frame()
        top = self._main_height() - (frame.origin.y + frame.size.height)
        rect = (round(frame.origin.x), round(top), round(frame.size.width), round(frame.size.height))
        return rect, bool(native.isZoomed())

    def minimized(self, window) -> bool:
        return bool(window.native.isMiniaturized())

    def focused(self, window) -> bool:
        from AppKit import NSApp

        return bool(NSApp.isActive() and window.native.isKeyWindow())

    def hide_now(self, window) -> None:
        self.on_ui(window, lambda: window.native.orderOut_(None))

    def bring_forward(self, window) -> None:
        # Opened by the shortcut while another app is in front, the window would come up behind it.
        from AppKit import NSApp

        def forward():
            NSApp.activateIgnoringOtherApps_(True)
            window.native.makeKeyAndOrderFront_(None)

        self.on_ui(window, forward)

    def title_bar(self, window, dark: bool) -> None:
        from AppKit import NSApp, NSAppearance

        name = "NSAppearanceNameDarkAqua" if dark else "NSAppearanceNameAqua"
        self.on_ui(window, lambda: NSApp.setAppearance_(NSAppearance.appearanceNamed_(name)))


class _LinuxHost:
    """What the window needs from Linux: Qt (PySide6, its WebEngine behind pywebview's Qt backend). Sizes are Qt's
    device-independent pixels. On Wayland a window can't place itself or take the focus: the compositor decides."""

    GUI = "qt"

    def app_id(self) -> None:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.setApplicationName("Dowse")
        QGuiApplication.setDesktopFileName("dowse")   # the taskbar's icon and name: dowse.desktop's

    def cursor_screen(self) -> tuple[float, float, float, float]:
        """The available area (without panels) of the screen the mouse is on."""
        from PySide6.QtGui import QCursor, QGuiApplication

        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        return area.x(), area.y(), area.x() + area.width(), area.y() + area.height()

    def scale(self, window) -> float:
        return 1.0   # pywebview works in Qt's device-independent pixels

    def on_ui(self, window, apply) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        done = threading.Event()

        def run():
            try:
                apply()
            finally:
                done.set()

        QTimer.singleShot(0, QApplication.instance(), run)
        done.wait(5)

    def minimum(self, window, size: tuple[int, int]) -> None:
        self.on_ui(window, lambda: window.native.setMinimumSize(*size))

    def topmost(self, window, on_top: bool) -> None:
        self.on_ui(window, lambda: setattr(window, "on_top", on_top))

    def bounds(self, window) -> tuple[tuple[int, int, int, int], bool]:
        native = window.native
        frame = native.frameGeometry()
        return (frame.x(), frame.y(), frame.width(), frame.height()), bool(native.isMaximized())

    def minimized(self, window) -> bool:
        return bool(window.native.isMinimized())

    def focused(self, window) -> bool:
        return bool(window.native.isActiveWindow())

    def hide_now(self, window) -> None:
        self.on_ui(window, lambda: window.native.hide())

    def bring_forward(self, window) -> None:
        def forward():
            native = window.native
            native.showNormal() if native.isMinimized() else native.show()
            native.raise_()
            native.activateWindow()

        self.on_ui(window, forward)

    def title_bar(self, window, dark: bool) -> None:
        """Qt's own parts (dialogs, scroll bars) in the app's look (Qt 6.8 and later); the title bar is the desktop's."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        hints = QGuiApplication.styleHints()
        if hasattr(hints, "setColorScheme"):
            scheme = Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light
            self.on_ui(window, lambda: hints.setColorScheme(scheme))


HOST = _MacHost() if sys.platform == "darwin" else _WindowsHost() if sys.platform == "win32" else \
    _LinuxHost() if sys.platform.startswith("linux") else None


class Windows:
    def __init__(self, url: str, token: str):
        HOST.app_id()
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

    def _minimum(self, size: tuple[int, int]) -> None:
        if not self.window.events.shown.wait(20):
            raise RuntimeError("the window didn't initialize")
        HOST.minimum(self.window, size)

    def _topmost(self, on_top: bool) -> None:
        HOST.topmost(self.window, on_top)

    def _remember(self) -> None:
        if self.presented:
            self.bounds[self.mode], self.maximized[self.mode] = HOST.bounds(self.window)

    def _place(self, mode: str) -> None:
        left, top, right, bottom = HOST.cursor_screen()
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
            HOST.bring_forward(self.window)
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
        if not self.visible or HOST.minimized(self.window):
            self.window.restore()
            if self.maximized.get(self.mode):
                self.window.maximize()
        self._topmost(False)
        self.window.show()
        HOST.bring_forward(self.window)
        self.visible = True
        self.window.evaluate_js("window.dispatchEvent(new Event('dowse:shown'))")
        self.last_shown = time.monotonic()
        return True

    def focused(self) -> bool:
        if not self.window.events.shown.wait(20):
            return False
        return HOST.focused(self.window)

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
        self._remember()
        HOST.hide_now(self.window)
        self.visible = False
        self.last_shown = time.monotonic()
        return False

    def title_bar(self) -> None:
        with contextlib.suppress(Exception):
            HOST.title_bar(self.window, self.dark)

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

    webview.start(started, gui=HOST.GUI, icon=str(APP_PNG if HOST.GUI == "qt" else ICON), private_mode=False,
                  storage_path=str(paths.home() / "WebView"))


if __name__ == "__main__":
    main()
