import os
import sys

import pytest

from dowse import crawler, rules
from dowse.rules import Kind
from dowse.selection import Selection


def test_classify():
    assert rules.classify(r"C:\x\IMG_1234.JPG") is Kind.IMAGE
    assert rules.classify(r"C:\x\Screenshot (12).png") is Kind.SCREENSHOT
    assert rules.classify(r"C:\Users\me\Pictures\Screenshots\anything.png") is Kind.SCREENSHOT
    assert rules.classify("lease.pdf") is Kind.PDF
    assert rules.classify("minutes.docx") is Kind.DOC
    assert rules.classify("song.mp3") is Kind.AUDIO
    assert rules.classify("clip.mkv") is Kind.VIDEO
    assert rules.classify("id_rsa") == "key or password file"
    assert rules.classify("server.pem") == "key or password file"
    assert rules.classify(".env.local") == "key or password file"
    assert rules.classify("data.json") is None
    assert rules.classify("photo.jpg", rules.Skip.of(extensions=["jpg"])) == rules.SKIPPED_TYPE


def test_machine_names():
    assert rules.looks_machine_named("frame_000123")
    assert rules.looks_machine_named("3f9a1c0e-4b2d")
    assert not rules.looks_machine_named("IMG_1234")
    assert not rules.looks_machine_named("holiday at the beach")


def test_dataset_folders():
    files = [("D:/data/clips", f"clip_{i:06d}", Kind.IMAGE) for i in range(400)]
    files += [("D:/photos", f"IMG_{i}", Kind.IMAGE) for i in range(400)]
    assert rules.dataset_folders(files, roots=set()) == {"D:/data/clips": 400}
    assert rules.dataset_folders(files, roots={"D:/data/clips"}) == {}


def test_crawl(tmp_path):
    def touch(relative: str) -> None:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    for name in ["photos/IMG_1.jpg", "docs/a.pdf", "docs/notes.txt", "node_modules/pkg/readme.md",
                 ".hidden/secret.png", "keys/id_ed25519", "project/package.json", "project/README.md",
                 "project/Screenshot 1.png", "data.json"]:
        touch(name)
    found = crawler.crawl(Selection((str(tmp_path),)))
    paths = sorted(os.path.relpath(f.path, tmp_path).replace(os.sep, "/") for f in found.files)
    assert paths == ["docs/a.pdf", "docs/notes.txt", "photos/IMG_1.jpg", "project/Screenshot 1.png"]
    assert found.skipped["key or password file"] == 1
    assert found.skipped["inside a code project"] == 1
    assert found.skipped["system or developer folder"] == 1
    assert found.skipped["hidden folder"] == 1


def test_moving_from_the_old_name(tmp_path, monkeypatch):
    """The old data folder becomes the new one, and what pointed inside it points at the new place."""
    from PIL import Image

    from dowse import migrate, paths, settings
    from dowse.indexer import Indexer
    from dowse.store import Store
    from test_index import FakeEmbedder

    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("DOWSE_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")   # no registry here
    monkeypatch.setattr(paths, "home", lambda: local / "Dowse")
    old = local / "SearchPC"
    (old / "Testbed").mkdir(parents=True)
    Image.new("RGB", (400, 300), (5, 0, 0)).save(old / "Testbed" / "IMG_1.png")
    monkeypatch.setattr(paths, "home", lambda: old)          # as the old version had it
    settings.save({"folders": [str(old / "Testbed")], "hotkey": "Alt+Shift+Space"})
    store = Store(old / "Index" / "index.sqlite")
    indexer = Indexer(store, workers=1)
    indexer.sync(Selection((str(old / "Testbed"),)))
    indexer.run(FakeEmbedder())
    file_id, vectors = store.files()[0].id, store.vector_count()
    store.close()

    monkeypatch.setattr(paths, "home", lambda: local / "Dowse")
    assert migrate.run() and not old.exists()
    assert settings.load()["folders"] == [str(local / "Dowse" / "Testbed")]
    moved = Store(local / "Dowse" / "Index" / "index.sqlite")
    assert [(f.id, f.path) for f in moved.files()] == [(file_id, str(local / "Dowse" / "Testbed" / "IMG_1.png"))]
    assert moved.vector_count() == vectors
    assert not migrate.run()   # once only


def test_moving_into_a_new_home_made_too_early(tmp_path, monkeypatch):
    """The new home already exists with an empty index (made before the move): the real one still moves in, and a
    locked item stays behind for a later start without stopping the rest."""
    from dowse import migrate, paths
    from dowse.store import Store

    local = tmp_path / "Local"
    monkeypatch.delenv("DOWSE_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    old, new = local / "SearchPC", local / "Dowse"
    (old / "Models").mkdir(parents=True)
    (old / "Models" / "model.gguf").write_bytes(b"x" * 10)
    real = Store(old / "Index" / "index.sqlite")
    with real.transaction():
        real.add_file(str(old / "notes.txt"), __import__("dowse.rules").rules.Kind.DOC, 1, 1.0)
    real.close()
    Store(new / "Index" / "index.sqlite").close()               # the empty index `dowse status` made
    monkeypatch.setattr(paths, "home", lambda: new)
    assert migrate.run()
    assert (new / "Models" / "model.gguf").exists() and not old.exists()
    moved = Store(new / "Index" / "index.sqlite")
    assert [f.path for f in moved.files()] == [str(new / "notes.txt")]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' file encryption")
def test_encrypting_the_index(tmp_path):
    from dowse import privacy

    if not privacy.supported(tmp_path):
        pytest.skip("this edition of Windows can't encrypt files (Home)")
    folder = tmp_path / "Index"
    folder.mkdir()
    (folder / "index.sqlite").write_bytes(b"x" * 100)
    bucket = folder / "Pictures" / "ab"
    bucket.mkdir(parents=True)
    (bucket / "old.webp").write_bytes(b"old picture")
    privacy.apply(True, [folder])
    assert privacy.encrypted(folder) and privacy.encrypted(folder / "index.sqlite")
    assert privacy.encrypted(folder / "Pictures") and privacy.encrypted(bucket)
    assert privacy.encrypted(bucket / "old.webp")
    (folder / "index.sqlite-wal").write_bytes(b"y")                        # made later, in the folder: encrypted too
    assert privacy.encrypted(folder / "index.sqlite-wal")
    (bucket / "new.webp").write_bytes(b"new picture")                     # an existing subfolder inherits too
    temporary = bucket / "old.tmp"
    temporary.write_bytes(b"replacement")
    temporary.replace(bucket / "old.webp")
    assert privacy.encrypted(bucket / "new.webp") and privacy.encrypted(bucket / "old.webp")
    assert (folder / "index.sqlite").read_bytes() == b"x" * 100             # as you, it reads as ever
    privacy.apply(False, [folder])
    assert not privacy.encrypted(folder) and not privacy.encrypted(folder / "index.sqlite")
    assert not privacy.encrypted(folder / "Pictures") and not privacy.encrypted(bucket)
    assert not privacy.encrypted(bucket / "old.webp") and not privacy.encrypted(bucket / "new.webp")
    (bucket / "later.webp").write_bytes(b"later")
    assert not privacy.encrypted(bucket / "later.webp")
