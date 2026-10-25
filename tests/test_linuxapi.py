"""What the app asks of Linux (`desktop.linuxapi`): files of the freedesktop.org standards, read and written in a
scratch home, so these run on any system."""

import os
import shlex

import pytest

from dowse.desktop import linuxapi, winapi


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(linuxapi.Path, "home", lambda: tmp_path)
    return tmp_path


def exec_line(entry) -> list[str]:
    line = next(line for line in entry.read_text().splitlines() if line.startswith("Exec="))
    return shlex.split(line[len("Exec="):])


def test_opening_at_login_is_an_autostart_entry(xdg):
    program = str(xdg / "My Apps" / "Dowse.AppImage")
    linuxapi.set_starts_at_login(True, program, [])
    assert linuxapi.starts_at_login()
    entry = xdg / "config" / "autostart" / "dowse.desktop"
    assert exec_line(entry) == [program, "--background"]   # a space in the path stays one argument
    linuxapi.set_starts_at_login(False, program, [])
    assert not linuxapi.starts_at_login()


def test_an_appimage_puts_itself_in_the_apps_menu_and_login_follows_it(xdg, monkeypatch):
    image = xdg / "Downloads" / "Dowse.AppImage"
    image.parent.mkdir()
    image.write_bytes(b"ELF")
    linuxapi.set_starts_at_login(True, "/tmp/.mount_Dowse-old/usr/python/bin/python3", ["-m", "dowse.desktop.launch"])
    monkeypatch.setenv("APPIMAGE", str(image))
    linuxapi.integrate()
    icon = xdg / "data" / "icons" / "hicolor" / "256x256" / "apps" / "dowse.png"
    menu = xdg / "data" / "applications" / "dowse.desktop"
    assert icon.read_bytes() == linuxapi.APP_PNG.read_bytes()
    assert exec_line(menu) == [str(image)] and f"Icon={icon}" in menu.read_text()
    assert exec_line(xdg / "config" / "autostart" / "dowse.desktop") == [str(image), "--background"]


def test_run_from_an_appimage_dowse_starts_again_as_the_appimage(monkeypatch):
    monkeypatch.setattr(winapi, "LINUX", True)
    monkeypatch.setenv("APPIMAGE", "/opt/apps/Dowse.AppImage")
    assert winapi.launcher() == ("/opt/apps/Dowse.AppImage", [])


def test_the_users_folders_are_where_user_dirs_says(xdg):
    (xdg / "config").mkdir()
    (xdg / "config" / "user-dirs.dirs").write_text('XDG_PICTURES_DIR="$HOME/Bilder"\nXDG_DESKTOP_DIR="$HOME/"\n')
    assert linuxapi.known_folder("Pictures") == os.path.join(str(xdg), "Bilder")
    assert linuxapi.known_folder("Desktop") == os.path.join(str(xdg), "Desktop")   # "$HOME/" means none set
    (xdg / "Bilder" / "Screenshots").mkdir(parents=True)
    assert linuxapi.screenshots_folder() == os.path.join(str(xdg), "Bilder", "Screenshots")


def test_on_battery_and_power_saver_from_the_kernel(tmp_path, monkeypatch):
    monkeypatch.setattr(linuxapi, "_run", lambda command, timeout=5: "power-saver")
    for name, kind, online in (("AC", "Mains", "0"), ("BAT0", "Battery", None)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "type").write_text(kind)
        if online is not None:
            (tmp_path / name / "online").write_text(online)
    assert linuxapi.power(tmp_path) == (True, True)
    (tmp_path / "AC" / "online").write_text("1")
    monkeypatch.setattr(linuxapi, "_run", lambda command, timeout=5: "balanced")
    assert linuxapi.power(tmp_path) == (False, False)


def test_opening_where_it_matched_with_the_apps_that_can_be_told():
    assert linuxapi.open_command("/m/a b.pdf", "pdf", 4.0, "org.kde.okular.desktop") == ["okular", "--page", "4",
                                                                                         "/m/a b.pdf"]
    assert linuxapi.open_command("/m/song.mp3", "audio", 75.6, "vlc.desktop") == ["vlc", "--start-time=75", "/m/song.mp3"]
    assert linuxapi.open_command("/m/a.pdf", "pdf", None, "org.kde.okular.desktop") is None   # no place: just open it
    assert linuxapi.open_command("/m/a.pdf", "pdf", 2.0, "libreoffice-draw.desktop") is None
