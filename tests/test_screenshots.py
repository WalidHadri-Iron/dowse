"""New screenshots into Dowse: a picture saved in a screenshot folder while the switch is on is added by name, once
it's whole; those there before, other files, and pictures in its subfolders aren't; with the switch off, nothing is."""

import json
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

import test_desktop
from dowse.desktop import screenshots
from dowse.desktop.screenshots import ScreenshotWatcher
from test_desktop import request

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows' folder notifications")
app = test_desktop.app


def wait_for(condition, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


def shot(path: Path, shade: int = 40) -> Path:
    Image.new("RGB", (320, 200), (shade, shade, 90)).save(path)
    return path


def test_only_screenshots_saved_after_the_switch_are_handed_over(tmp_path):
    folder = tmp_path / "Screenshots"
    folder.mkdir()
    shot(folder / "Screenshot (1).png")   # there before: the user's to add
    time.sleep(1.2)
    found: list[str] = []
    watcher = ScreenshotWatcher(found.extend, roots=[str(folder)], quiet=0.3)
    watcher.start()
    try:
        time.sleep(0.3)
        new = shot(folder / "Screenshot (2).png")
        (folder / "notes.txt").write_text("not a picture")
        (folder / "edited").mkdir()
        shot(folder / "edited" / "inside.png")   # a subfolder's: not a screenshot Windows saved
        assert wait_for(lambda: found)
        time.sleep(1.0)
        assert found == [str(new)]
    finally:
        watcher.stop()
    shot(folder / "Screenshot (3).png")   # stopped: nothing more
    time.sleep(1.0)
    assert found == [str(new)]


def test_a_screenshot_folder_made_later_is_watched_once_it_is_there(tmp_path, monkeypatch):
    monkeypatch.setattr(screenshots, "LOOK_AGAIN", 0.5)
    folder = tmp_path / "Screenshots"
    found: list[str] = []
    watcher = ScreenshotWatcher(found.extend, roots=[str(folder)], quiet=0.3)
    watcher.start()
    try:
        folder.mkdir()   # the first Win+PrtScn on this PC makes it
        time.sleep(1.5)
        new = shot(folder / "Screenshot (1).png")
        assert wait_for(lambda: found == [str(new)])
    finally:
        watcher.stop()


def test_the_settings_switch_adds_new_screenshots_to_dowsed_files(app, tmp_path, monkeypatch):
    running, _, _ = app
    folder = tmp_path / "Screenshots"
    folder.mkdir()
    monkeypatch.setattr(screenshots, "folders", lambda: [str(folder)])
    monkeypatch.setattr(screenshots, "QUIET", 0.3)
    monkeypatch.setattr("dowse.desktop.app.ScreenshotWatcher",
                        lambda on_new: ScreenshotWatcher(on_new, roots=[str(folder)], quiet=0.3))
    assert json.loads(request(running.server, "GET", "/api/state")[2])["settings"]["screenshots"] is False
    status, _, raw = request(running.server, "POST", "/api/settings", {"screenshots": True})
    assert status == 200 and json.loads(raw)["state"]["settings"]["screenshots"] is True
    time.sleep(0.3)
    new = shot(folder / "Screenshot 2026-10-15 120000.png")
    assert wait_for(lambda: str(new) in running.selection.files)
    request(running.server, "POST", "/api/settings", {"screenshots": False})
    assert running.screenshots is None
    later = shot(folder / "Screenshot 2026-10-15 120500.png", 80)
    time.sleep(1.5)
    assert str(later) not in running.selection.files
