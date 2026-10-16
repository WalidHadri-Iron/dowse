"""Encryption covers existing cache directories, and a failed change must stop startup."""

import pytest

from dowse import privacy
from dowse.desktop import main


def test_existing_cache_directories_are_marked_in_both_directions(tmp_path, monkeypatch):
    folder = tmp_path / "Index"
    bucket = folder / "Pictures" / "ab"
    bucket.mkdir(parents=True)
    picture = bucket / "old.webp"
    picture.write_bytes(b"picture")
    marked, calls = set(), []
    monkeypatch.setattr(privacy, "WINDOWS", True)
    monkeypatch.setattr(privacy, "encrypted", lambda p: p in marked)

    def change(path, on):
        calls.append((path, on))
        (marked.add if on else marked.discard)(path)
        return True

    monkeypatch.setattr(privacy, "_encrypt", change)
    privacy.apply(True, [folder])
    assert marked == {folder, folder / "Pictures", bucket, picture}
    assert calls.index((bucket, True)) < calls.index((picture, True))
    privacy.apply(True, [folder])   # already applied: no extra Windows calls
    assert len(calls) == 4
    privacy.apply(False, [folder])
    assert not marked and len(calls) == 8


@pytest.mark.parametrize("reported_success", [False, True])
def test_failed_or_ineffective_encryption_is_not_silently_accepted(tmp_path, monkeypatch, reported_success):
    monkeypatch.setattr(privacy, "WINDOWS", True)
    monkeypatch.setattr(privacy, "encrypted", lambda p: False)
    monkeypatch.setattr(privacy, "_encrypt", lambda p, on: reported_success)
    with pytest.raises(OSError, match="couldn't encrypt"):
        privacy.apply(True, [tmp_path / "Index"])


def test_unreadable_subtree_is_not_silently_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "WINDOWS", True)

    def walk(path, *, onerror):
        onerror(PermissionError("cache folder is locked"))
        return iter(())

    monkeypatch.setattr(privacy.os, "walk", walk)
    with pytest.raises(PermissionError, match="cache folder is locked"):
        privacy.apply(True, [tmp_path])


@pytest.mark.parametrize("on", [False, True])
def test_failed_privacy_change_stops_before_logs_or_app_open(monkeypatch, on):
    errors = []
    monkeypatch.setattr(main.migrate, "run", lambda: None)
    monkeypatch.setattr(main.settings, "load", lambda: {"encrypt_index": on})

    def fail(requested):
        assert requested is on
        raise OSError("Windows refused the encryption change")

    monkeypatch.setattr(main.privacy, "apply", fail)
    monkeypatch.setattr(main, "_logging", lambda: pytest.fail("must not open logs after encryption failed"))
    monkeypatch.setattr(main.winapi, "startup_error", errors.append)
    assert main.run() == 1
    assert len(errors) == 1 and "has not started" in errors[0] and "Windows refused" in errors[0]
