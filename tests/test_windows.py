"""The single-window lifecycle without a GUI: mode switches, explicit dismissal, and shortcut focus."""

from types import SimpleNamespace

import pytest

from dowse.desktop import windows


class Event:
    def __iadd__(self, callback):
        return self


class Window:
    def __init__(self, title, url, **options):
        self.events = SimpleNamespace(closing=Event(), shown=Event())
        self.native = SimpleNamespace(DeviceDpi=96, RestoreBounds=SimpleNamespace(
            X=0, Y=0, Width=options["width"], Height=options["height"]),
            WindowState=SimpleNamespace(ToString=lambda: "Normal"), Hide=self.hide)
        self.native.Bounds = self.native.RestoreBounds
        self.url = url
        self.scripts = []
        self.shown = False
        self.on_top = False

    def resize(self, width, height):
        self.native.RestoreBounds.Width, self.native.RestoreBounds.Height = width, height

    def move(self, x, y):
        self.native.RestoreBounds.X, self.native.RestoreBounds.Y = x, y

    def set_title(self, title):
        self.title = title

    def restore(self):
        self.native.WindowState = SimpleNamespace(ToString=lambda: "Normal")

    def maximize(self):
        self.native.WindowState = SimpleNamespace(ToString=lambda: "Maximized")

    def show(self):
        self.shown = True

    def hide(self):
        self.shown = False

    def evaluate_js(self, script):
        self.scripts.append(script)


class FakeHost(windows._WindowsHost):
    """The real Windows side (`windows.HOST`), WinForms faked: one 1920×1080 screen at `dpi` scale, and UI-thread
    dispatch done at once (or handed to `dispatch`)."""

    def __init__(self):   # (no user32: the fakes stand in for WinForms)
        self.dpi = 1.0
        self.dispatch = None

    def cursor_screen(self):
        return 0, 0, 1920, 1080

    def scale(self, window):
        return self.dpi

    def on_ui(self, window, apply):
        (self.dispatch or (lambda run: run()))(apply)

    def title_bar(self, window, dark):
        pass


@pytest.fixture
def host(monkeypatch):
    created = []

    def create(*args, **kwargs):
        window = Window(*args, **kwargs)
        created.append(window)
        return window

    monkeypatch.setattr(windows.webview, "create_window", create)
    monkeypatch.setattr(windows, "HOST", FakeHost())
    monkeypatch.setattr(windows.Windows, "_minimum", lambda self, size: setattr(self, "minimum", size))
    monkeypatch.setattr(windows.Windows, "focused", lambda self: False)
    return windows.Windows("http://127.0.0.1:1234/", "test-token"), created


def test_expanding_and_collapsing_reuse_one_window_and_remember_each_size(host):
    app, created = host
    app.show_popup()
    app.window.resize(800, 520)
    app.window.move(120, 80)
    app.show_main("/search?q=lease&in=C%3A%5CDocuments")
    assert app.minimum == windows.MAIN_MIN
    assert not app.window.on_top
    app.window.resize(1400, 900)
    app.show_popup("/popup?q=lease&in=C%3A%5CDocuments")
    assert app.minimum == windows.POPUP_MIN
    assert not app.window.on_top
    assert app.bounds["compact"] == (120, 80, 800, 520)
    assert app.window.native.RestoreBounds.Width == 800
    app.show_main("/library")
    assert app.window.native.RestoreBounds.Width == 1400
    assert len(created) == 1 and app.window is created[0]
    assert app.window.url.endswith("#/popup")   # changing mode never reloads the page


def test_window_process_sets_its_own_identity_before_creating_the_form(host, monkeypatch):
    events = []
    create = windows.webview.create_window
    monkeypatch.setattr(windows.winapi, "set_app_id", lambda identity: events.append(("identity", identity)))
    def branded_window(*args, **kwargs):
        events.append(("window", args[0]))
        return create(*args, **kwargs)
    monkeypatch.setattr(windows.webview, "create_window", branded_window)
    windows.Windows("http://127.0.0.1:1234/", "test-token")
    assert events[0] == ("identity", "Dowse.App")
    assert events[1][0] == "window"


def test_production_webview_starts_with_the_packaged_icon(monkeypatch):
    if windows.HOST is None:
        pytest.skip("no window host on this system yet (Windows and macOS)")
    options = {}
    monkeypatch.setattr(windows.sys, "argv", ["windows", "http://127.0.0.1:1234/", "test-token"])
    monkeypatch.setattr(windows, "Windows", lambda *args: SimpleNamespace())
    monkeypatch.setattr(windows.webview, "start", lambda callback, **kwargs: options.update(kwargs))
    windows.main()
    assert options["icon"] == str(windows.ICON)
    assert options["gui"] == windows.HOST.GUI   # WebView2 on Windows, WKWebView on a Mac
    assert windows._WindowsHost.GUI == "edgechromium" and windows._MacHost.GUI == "cocoa"


def test_explicit_popup_dismissal_cannot_hide_a_workspace_or_file_dialog(host):
    app, _ = host
    app.show_main("/search")
    assert app.hide_popup() is False and app.visible
    app.show_popup()
    app.picking = True
    assert app.hide_popup() is False and app.visible
    app.picking = False
    assert app.hide_popup() is True and not app.visible
    app.show_popup()
    assert app.closing() is False and not app.visible   # close hides; Quit may destroy
    app.quitting = True
    assert app.closing() is True


def test_shortcut_focuses_the_existing_mode(host, monkeypatch):
    app, created = host
    app.show_main("/library")
    app.toggle()
    assert app.mode == "full" and app.visible
    monkeypatch.setattr(app, "focused", lambda: True)
    app.toggle()
    assert not app.visible
    app.toggle()
    assert app.visible and app.mode == "full" and len(created) == 1


def test_workspace_maximization_survives_a_round_trip_to_quick_search(host):
    app, _ = host
    app.show_main("/search")
    app.window.maximize()
    app.show_popup()
    assert app.window.native.WindowState.ToString() == "Normal"
    app.show_main("/search")
    assert app.window.native.WindowState.ToString() == "Maximized"


def test_saved_geometry_uses_the_window_dpi(host, monkeypatch):
    app, _ = host
    app.show_popup()
    windows.HOST.dpi = 1.5
    app.window.resize(1200, 780)
    app.window.move(180, 120)
    app._remember()
    assert app.bounds["compact"] == (120, 80, 800, 520)


def test_normal_geometry_ignores_stale_winforms_restore_bounds(host):
    app, _ = host
    app.show_popup()
    app.window.resize(800, 520)
    app.window.move(120, 80)
    app.window.native.RestoreBounds = SimpleNamespace(X=0, Y=0, Width=640, Height=440)
    app._remember()
    assert app.bounds["compact"] == (120, 80, 800, 520)


def test_topmost_changes_are_dispatched_to_the_ui_thread(host, monkeypatch):
    app, _ = host
    pending = []
    windows.HOST.dispatch = pending.append
    app._topmost(True)
    assert not app.window.on_top   # a background command must not touch WinForms before UI dispatch
    assert len(pending) == 1
    pending.pop()()
    assert app.window.on_top


@pytest.mark.parametrize("mode", ["compact", "full"])
def test_both_modes_yield_to_other_apps_and_clear_stale_topmost_state(host, mode):
    app, created = host
    app.window.on_top = True
    app.show(mode)
    assert app.visible and app.window.shown
    assert not app.window.on_top
    assert len(created) == 1


def test_shortcut_brings_quick_search_forward_without_pinning_it_above_other_apps(host, monkeypatch):
    app, created = host
    app.show_popup()
    monkeypatch.setattr(app, "focused", lambda: True)
    assert app.toggle() and not app.visible
    app.window.on_top = True
    monkeypatch.setattr(app, "focused", lambda: False)
    assert app.toggle() and app.visible
    assert app.mode == "compact" and not app.window.on_top
    assert len(created) == 1
