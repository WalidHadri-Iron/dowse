"""The app's presence on the desktop: the tray icon (a Mac's menu bar), the global shortcut, and its one window.

The window lives in a process of its own (`windows`), started on demand and gone after ten minutes hidden:
WebView2 holds ~240 MB for as long as a window exists, and Dowse at rest is the tray icon. That process
is tied to this one (a kill-on-close job), so it never outlives the app.

- The shortcut is registered on a thread of its own (RegisterHotKey), which also re-registers it when it changes.
  On a Mac it's a Carbon hotkey (RegisterEventHotKey: no Accessibility permission needed), on the main run loop.
- The tray menu shows how indexing is going and leads everywhere; left-click opens search.
- On a Mac, AppKit must own the main thread: the menu-bar icon runs there, and the app is an "accessory" (no Dock
  icon; its window process has its own).
"""

import ctypes
import itertools
import json
import logging
import subprocess
import sys
import threading

from .. import runtime
from . import winapi
from .brand import APP_PNG, ICON, MENU_BAR_ICON
from .indexing import Status

log = logging.getLogger("dowse.native")

WM_HOTKEY, WM_APP = 0x0312, 0x8000



def _menu_bar_icon(base):
    """pystray's Mac icon, drawn as macOS draws menu-bar icons: a template image (black and clear, coloured by the
    system for a light or dark menu bar, highlighted when open), 18 points in the bar's 22, sharp on Retina."""
    class MenuBarIcon(base):
        def _assert_image(self):
            if self._icon_image is not None:
                return
            import io

            import AppKit
            import Foundation

            data = io.BytesIO()
            self._icon.save(data, "png")
            image = AppKit.NSImage.alloc().initWithData_(Foundation.NSData(data.getvalue()))
            image.setSize_((18, 18))
            image.setTemplate_(True)
            self._icon_image = image
            self._status_item.button().setImage_(image)

    return MenuBarIcon


def _in_background(action) -> None:
    threading.Thread(target=action, daemon=True).start()


class QtTray:
    """Linux's tray icon: Qt's, which speaks the tray protocol of today's desktops (StatusNotifierItem: KDE, GNOME
    with the AppIndicator extension, Cinnamon, XFCE…) and the old X11 one. It answers the calls `Native` makes of a
    pystray icon (`title`, `update_menu`, `notify`, `run`, `stop`), from any thread: each is done on Qt's."""

    def __init__(self, entries: list, default):
        from PySide6.QtGui import QAction, QGuiApplication, QIcon
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

        QGuiApplication.setApplicationName("Dowse")
        QGuiApplication.setDesktopFileName("dowse")
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        self.app.setQuitOnLastWindowClosed(False)   # (it has no windows: they're in their own process)
        self.icon = QSystemTrayIcon(QIcon(str(APP_PNG)))
        self.icon.setToolTip("Dowse")
        self.menu = QMenu()
        self._labels, self._checks = [], []
        for entry in entries:
            if entry is None:
                self.menu.addSeparator()
                continue
            label, action, checked = entry
            item = QAction(label() if callable(label) else label, self.menu)
            if action is None:
                item.setEnabled(False)
            else:   # off Qt's thread, as pystray calls them: an action may wait on the window's process
                item.triggered.connect(lambda _=False, action=action: _in_background(action))
            if callable(label):
                self._labels.append((item, label))
            if checked:
                item.setCheckable(True)
                self._checks.append((item, checked))
            self.menu.addAction(item)
        self.menu.aboutToShow.connect(self._refresh)
        self.icon.setContextMenu(self.menu)
        clicks = (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick)
        self.icon.activated.connect(lambda reason: _in_background(default) if reason in clicks else None)
        self._refresh()
        self.icon.show()

    def _later(self, apply) -> None:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self.app, apply)

    def _refresh(self) -> None:
        for item, label in self._labels:
            item.setText(label())
        for item, checked in self._checks:
            item.setChecked(bool(checked()))

    def available(self) -> bool:
        from PySide6.QtWidgets import QSystemTrayIcon

        return QSystemTrayIcon.isSystemTrayAvailable()

    @property
    def title(self) -> str:
        return self.icon.toolTip()

    @title.setter
    def title(self, text: str) -> None:
        self._later(lambda: self.icon.setToolTip(text))

    def update_menu(self) -> None:
        self._later(self._refresh)

    def notify(self, message: str, title: str) -> None:
        self._later(lambda: self.icon.showMessage(title, message))

    def run(self, setup=None) -> None:
        if setup is not None:
            self._later(setup)
        self.app.exec()

    def stop(self) -> None:
        self._later(self.app.quit)

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
        import ctypes.wintypes as w
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


class MacHotkey:
    """The global shortcut on a Mac: a Carbon hotkey on the application's event target, registered and changed on the
    main run loop (where Carbon delivers it). The same contract as `Hotkey`."""

    SIGNATURE = 0x44575345   # 'DWSE'
    _handler = None          # one handler for the app: kept alive here, as Carbon calls it for the app's life

    def __init__(self, text: str, pressed):
        from . import macapi

        self.macapi, self.text, self.pressed = macapi, text, pressed
        self._ref = ctypes.c_void_p()
        self._ok = False
        self._result = threading.Event()
        self._carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        self._on_main(lambda: self._first(text))

    def _on_main(self, work) -> None:
        from PyObjCTools import AppHelper

        self._result.clear()
        AppHelper.callAfter(work)

    class _ID(ctypes.Structure):
        _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]

    class _Spec(ctypes.Structure):
        _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]

    def _first(self, text: str) -> None:
        carbon = self._carbon
        handler_type = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, handler_type, ctypes.c_uint32,
                                               ctypes.POINTER(self._Spec), ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_void_p)]
        carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, self._ID, ctypes.c_void_p,
                                               ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]

        def on_press(_call, _event, _data):
            threading.Thread(target=self.pressed, daemon=True).start()   # never hold up the main thread
            return 0

        MacHotkey._handler = handler_type(on_press)
        spec = self._Spec(0x6B657962, 5)   # kEventClassKeyboard ('keyb'), kEventHotKeyPressed
        carbon.InstallEventHandler(carbon.GetApplicationEventTarget(), MacHotkey._handler, 1, ctypes.byref(spec),
                                   None, None)
        self._ok = self._register(text)
        self._result.set()

    def _register(self, text: str) -> bool:
        try:
            modifiers, key = self.macapi.parse_hotkey(text)
        except ValueError:
            return False
        ref = ctypes.c_void_p()
        status = self._carbon.RegisterEventHotKey(key, modifiers, self._ID(self.SIGNATURE, 1),
                                                  self._carbon.GetApplicationEventTarget(), 0, ctypes.byref(ref))
        if status != 0:   # eventHotKeyExistsErr: another app has it
            return False
        self._ref = ref
        return True

    def registered(self) -> bool:
        self._result.wait(5)
        return self._ok

    def change(self, text: str) -> bool:
        def swap():
            if self._ref:
                self._carbon.UnregisterEventHotKey(self._ref)
                self._ref = ctypes.c_void_p()
            if self._register(text):
                self.text, self._ok = text, True
            else:
                self._ok = False
                self._register(self.text)   # keep the old one
            self._result.set()

        self._on_main(swap)
        self._result.wait(5)
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
        if sys.platform == "darwin":
            return self._run_mac(show)
        if sys.platform.startswith("linux"):
            return self._run_linux(show)
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

    def _run_mac(self, show: str | None) -> None:
        """AppKit owns the main thread: the menu-bar icon's loop runs here until Quit, and what needs it running
        (the accessory policy, the shortcut on the main run loop) is done once the icon is up."""
        def started(icon) -> None:
            icon.visible = True
            from PyObjCTools import AppHelper

            def accessory():
                from AppKit import NSApp
                NSApp.setActivationPolicy_(1)   # NSApplicationActivationPolicyAccessory: no Dock icon

            AppHelper.callAfter(accessory)
            self.hotkey = MacHotkey(self.app.settings["hotkey"], self.toggle_popup)
            if not self.hotkey.registered():
                self.notify("Dowse", f"{self.app.settings['hotkey']} is taken by another app. Search from the menu "
                                     "bar icon, or choose another shortcut in Settings.")
            if show == "popup":
                self.show_popup()
            elif show:
                self.show_main(show)

        self._tray(start=False)
        self.tray.run(setup=started)
        self._quit.set()

    def _run_linux(self, show: str | None) -> None:
        """Qt owns the main thread: the tray icon's loop runs here until Quit. There's no global shortcut of Dowse's
        own (Wayland doesn't let an app take one): the desktop's keyboard settings can run `dowse-app`, which opens
        Quick search in the running app."""
        self.tray = QtTray(self._entries(), self.show_popup)

        def started() -> None:
            if not self.tray.available():
                self.notify("Dowse is running", "This desktop shows no tray icons: open Dowse from your apps.")
            if show == "popup":
                self.show_popup()
            elif show:
                self.show_main(show)

        self.tray.run(setup=started)
        self._quit.set()

    def _entries(self) -> list:
        """The tray's menu, for each system's tray to draw: (label, action, checked) or None between groups. The
        first entry with an action is the icon's own (a click)."""
        return [
            (lambda: self._status.text, None, None),
            None,
            ("Quick search", lambda: self.show_popup(), None),
            ("Workspace", lambda: self.show_main("/search"), None),
            ("Library", lambda: self.show_main("/library"), None),
            None,
            ("Pause indexing", lambda: self.app.change_settings({"paused": not self._paused}), lambda: self._paused),
            ("Settings", lambda: self.show_main("/settings"), None),
            None,
            ("Quit Dowse", lambda: self.quit(), None),
        ]

    def _tray(self, start: bool = True) -> None:
        import pystray
        from PIL import Image

        def of_item(read):   # pystray calls a label or a check mark with the item (and counts the parameters)
            return lambda _item: read()

        def run(action):     # and an action with none
            return lambda: action()

        items, default = [], True
        for entry in self._entries():
            if entry is None:
                items.append(pystray.Menu.SEPARATOR)
                continue
            label, action, checked = entry
            text = of_item(label) if callable(label) else label
            if action is None:
                items.append(pystray.MenuItem(text, None, enabled=False))
                continue
            items.append(pystray.MenuItem(text, run(action), default=default,
                                          checked=of_item(checked) if checked else None))
            default = False
        menu = pystray.Menu(*items)
        if sys.platform == "darwin":
            self.tray = _menu_bar_icon(pystray.Icon)("Dowse", Image.open(MENU_BAR_ICON), "Dowse", menu)
        else:
            self.tray = pystray.Icon("Dowse", Image.open(ICON), "Dowse", menu)
        if start:
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
        if sys.platform == "darwin":
            from . import macapi
            macapi.notify(title, message)
            return
        if sys.platform.startswith("linux"):
            from . import linuxapi
            if linuxapi.notify(title, message):
                return
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
