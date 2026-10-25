"""Small music-tag fixtures, optionally surrounding playable sound made by test_media."""
import struct


def synchsafe(value):
    return bytes((value >> shift) & 0x7f for shift in (21, 14, 7, 0))


def id3(path, fields=None, artwork=0, version=3):
    fields = fields or {"TIT2": "Midnight Lantern", "TPE1": "The Skylarks", "TALB": "Winter Stories",
                        "TPE2": "Northern Band", "TRCK": "4/12", "TYER": "2024", "TCON": "Folk"}
    def frame(name, content):
        size = synchsafe(len(content)) if version == 4 else len(content).to_bytes(4, "big")
        return name.encode() + size + b"\0\0" + content
    payload = frame("APIC", b"\0image/jpeg\0\3\0" + b"x" * artwork) if artwork else b""
    for name, text in fields.items():
        payload += frame(name, b"\3" + text.encode("utf-8") if version == 4 else b"\1" + text.encode("utf-16"))
    sound = path.read_bytes() if path.exists() else b""
    if sound.startswith(b"ID3"):
        old_size = sum(b << shift for b, shift in zip(sound[6:10], (21, 14, 7, 0)))
        sound = sound[10 + old_size:]
    path.write_bytes(b"ID3" + bytes([version, 0, 0]) + synchsafe(len(payload)) + payload + sound)
    return path


def block(kind, content, last=False):
    return bytes([kind | (0x80 if last else 0)]) + len(content).to_bytes(3, "big") + content


def comments(*values):
    vendor = b"Dowse tests"
    result = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(values))
    for value in values:
        encoded = value.encode("utf-8")
        result += struct.pack("<I", len(encoded)) + encoded
    return result


def flac(path, artwork=0):
    data = path.read_bytes() if path.exists() else b""
    if data.startswith(b"fLaC"):
        size = int.from_bytes(data[5:8], "big")
        streaminfo, at = data[8:8 + size], 8 + size
        last = bool(data[4] & 0x80)
        while not last:
            last = bool(data[at] & 0x80)
            at += 4 + int.from_bytes(data[at + 1:at + 4], "big")
        sound = data[at:]
    else:
        streaminfo, sound = b"\0" * 34, b""
    metadata = comments("TITLE=Starlight Drive", "ARTIST=The Night Owls", "ALBUM=Evening Ride",
                        "ALBUMARTIST=Night Studio", "TRACKNUMBER=2", "DATE=2025", "GENRE=Jazz",
                        "COMMENT=Not indexed", "artist=Second Artist")
    # A real PICTURE block: type, MIME, description, dimensions/depth/palette, byte count, image bytes.
    image = struct.pack(">II", 3, 10) + b"image/jpeg" + struct.pack(">IIIIII", 0, 100, 100, 24, 0, artwork)
    picture = block(6, image + b"x" * artwork) if artwork else b""
    path.write_bytes(b"fLaC" + block(0, streaminfo) + picture + block(4, metadata, last=True) + sound)
    return path
