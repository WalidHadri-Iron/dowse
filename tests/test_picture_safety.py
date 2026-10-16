"""Background thumbnail warming cannot download cloud-only originals or poison their later previews."""
from PIL import Image

from dowse.desktop import pictures
from dowse.rules import Kind


def test_direct_thumbnail_warming_never_reads_an_online_only_file(monkeypatch, tmp_path):
    cache = pictures.Pictures(folder=tmp_path / "Pictures")
    online = [True]
    monkeypatch.setattr(pictures, "attributes", lambda path: 0)
    monkeypatch.setattr(pictures, "cloud_only", lambda attrs: online[0])
    reads = []
    def render(*args):
        reads.append(args[0])
        return Image.new("RGB", (40, 30), "white")
    monkeypatch.setattr(pictures, "render", render)
    assert cache.get("cloud.jpg", Kind.IMAGE, 1, None, 96) is None
    assert reads == []
    online[0] = False
    assert cache.get("cloud.jpg", Kind.IMAGE, 1, None, 96) is not None
    assert reads == ["cloud.jpg"]
    online[0] = True
    assert cache.get("cloud.jpg", Kind.IMAGE, 1, None, 96) is not None  # cached bytes never hydrate the original
    assert reads == ["cloud.jpg"]


def test_pictures_made_once_are_kept_on_disk_and_trimmed(monkeypatch, tmp_path):
    monkeypatch.setattr(pictures, "attributes", lambda path: 0)
    monkeypatch.setattr(pictures, "cloud_only", lambda attrs: False)
    made = []

    def render(path, *args):
        made.append(path)
        if path == "black.mp4":
            return None                       # nothing to show: remembered as that
        if path == "locked.jpg":
            raise OSError("in use")           # can't be read now: tried again next time
        return Image.new("RGB", (40, 30), "white")

    monkeypatch.setattr(pictures, "render", render)
    first = pictures.Pictures(folder=tmp_path / "Pictures")
    data = first.get("a.jpg", Kind.IMAGE, 1, None, 96)
    assert data and first.get("black.mp4", Kind.VIDEO, 1, None, 96) is None
    assert first.get("locked.jpg", Kind.IMAGE, 1, None, 96) is None
    again = pictures.Pictures(folder=tmp_path / "Pictures")   # after a restart: from disk
    assert again.get("a.jpg", Kind.IMAGE, 1, None, 96) == data and again.get("black.mp4", Kind.VIDEO, 1, None, 96) is None
    again.get("locked.jpg", Kind.IMAGE, 1, None, 96)
    assert made == ["a.jpg", "black.mp4", "locked.jpg", "locked.jpg"]
    assert again.get("a.jpg", Kind.IMAGE, 2, None, 96) and made[-1] == "a.jpg"   # the file changed: made anew

    small = pictures.Pictures(folder=tmp_path / "Pictures", disk_bytes=1)
    small.trim()
    assert not any(f.is_file() for f in (tmp_path / "Pictures").rglob("*.webp"))
