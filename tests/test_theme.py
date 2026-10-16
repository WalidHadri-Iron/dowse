"""Saved appearance choices, live Windows changes, API validation and native-frame theme propagation."""

import json

import pytest

from dowse import settings
from dowse.desktop.app import App
from test_desktop import request


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWSE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("dowse.desktop.app.setup.needed", lambda: True)
    monkeypatch.setattr("dowse.desktop.app.runtime.planned_gpu", lambda: None)
    monkeypatch.setattr("dowse.desktop.app.winapi.starts_with_windows", lambda: False)
    windows = {"dark": False, "accent": "#123456"}
    monkeypatch.setattr("dowse.desktop.app.system.theme", lambda: dict(windows))
    apps = []

    def create():
        app = App()
        app.server.start()
        apps.append(app)
        return app

    yield create, windows
    for app in apps:
        app.stop()
        app.server.stop()


def change(app, mode):
    code, _, body = request(app.server, "POST", "/api/settings", {"theme": mode})
    return code, json.loads(body)


@pytest.mark.parametrize("mode", ["system", "light", "dark"])
@pytest.mark.parametrize("windows_dark", [False, True])
def test_theme_is_effective_at_startup_and_keeps_windows_accent(make_app, mode, windows_dark):
    create, windows = make_app
    windows["dark"] = windows_dark
    settings.save({"theme": mode})
    app = create()
    assert app.state()["settings"]["theme"] == mode
    assert app.state()["theme"] == {"dark": windows_dark if mode == "system" else mode == "dark", "accent": "#123456"}


def test_theme_changes_apply_live_are_saved_and_restore_on_restart(make_app):
    create, windows = make_app
    app = create()
    events, frames = [], []
    app.events.publish = lambda name, data: events.append((name, data))
    app.native = type("Frame", (), {"theme": lambda _, look: frames.append(look)})()
    assert app.state()["settings"]["theme"] == "system"
    code, answer = change(app, "dark")
    assert code == 200 and answer["ok"]
    assert answer["state"]["theme"]["dark"] is True
    assert settings.load()["theme"] == "dark"
    assert events == [("theme", {"dark": True, "accent": "#123456"})] and frames == [events[0][1]]
    assert create().state()["theme"]["dark"] is True
    # Windows changes cannot override an explicit app choice; accent still follows Windows.
    windows.update(dark=False, accent="#abcdef")
    app._update_theme()
    assert app.state()["theme"] == {"dark": True, "accent": "#abcdef"}
    code, answer = change(app, "system")
    assert code == 200 and answer["state"]["theme"]["dark"] is False
    windows["dark"] = True
    app._update_theme()
    assert app.state()["theme"]["dark"] is True
    assert change(app, "light")[1]["state"]["theme"]["dark"] is False
    app._update_theme()
    assert app.state()["theme"]["dark"] is False


@pytest.mark.parametrize("bad", ["auto", "", None, 1, [], {}])
def test_invalid_theme_is_rejected_without_changing_saved_or_live_settings(make_app, bad):
    create, _ = make_app
    app = create()
    change(app, "dark")
    before = settings.file().read_bytes()
    assert change(app, bad)[0] == 400
    assert settings.file().read_bytes() == before
    assert app.state()["settings"]["theme"] == "dark" and app.state()["theme"]["dark"] is True


def test_unknown_saved_theme_falls_back_to_windows(make_app):
    create, windows = make_app
    settings.save({"theme": "old-unsupported-theme"})
    windows["dark"] = True
    app = create()
    assert app.state()["settings"]["theme"] == "system" and app.state()["theme"]["dark"] is True


def test_save_failure_keeps_current_choice_and_theme(make_app, monkeypatch):
    create, _ = make_app
    app = create()
    change(app, "dark")
    before = settings.file().read_bytes()

    def fail(_):
        raise OSError("disk full")

    monkeypatch.setattr("dowse.desktop.app.settings.save", fail)
    with pytest.raises(OSError, match="disk full"):
        app.change_settings({"theme": "light"})
    assert app.state()["settings"]["theme"] == "dark" and app.state()["theme"]["dark"] is True
    assert settings.file().read_bytes() == before
