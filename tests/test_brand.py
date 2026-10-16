"""The native app and generated brand assets must not drift apart."""
import struct
from pathlib import Path

from dowse.desktop import brand, native, shell


def test_native_surfaces_share_the_canonical_multisize_icon():
    source = Path(__file__).resolve().parents[1] / "assets" / "brand" / "dowse.ico"
    assert native.ICON == shell.ICON == brand.ICON
    data = brand.ICON.read_bytes()
    assert data == source.read_bytes(), "The packaged icon must be assets/brand/dowse.ico"
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    sizes = {data[6 + i * 16] or 256 for i in range(count)}
    assert {16, 20, 24, 32, 40, 48, 64, 128, 256} <= sizes
