"""An executable's icon and version information, rewritten (Windows' own UpdateResource): the installer's Dowse.exe
is Python's pythonw.exe, which would show Python's icon and call itself "Python" in Task Manager.

    set_icon(exe, ico)                 every icon group replaced by the .ico's images
    set_version(exe, name, version)    FileDescription, ProductName and versions say Dowse
"""

import ctypes
import struct
from ctypes import wintypes
from pathlib import Path

RT_ICON, RT_GROUP_ICON, RT_VERSION = 3, 14, 16
LOAD_LIBRARY_AS_DATAFILE = 0x2

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
kernel32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
kernel32.UpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.LPVOID, wintypes.WORD,
                                     wintypes.LPVOID, wintypes.DWORD]
kernel32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
kernel32.LoadLibraryExW.restype = wintypes.HMODULE
kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]

NAMES = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPARAM)
LANGS = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, wintypes.LPVOID, wintypes.LPVOID, wintypes.WORD,
                           wintypes.LPARAM)
kernel32.EnumResourceNamesW.argtypes = [wintypes.HMODULE, wintypes.LPVOID, NAMES, wintypes.LPARAM]
kernel32.EnumResourceLanguagesW.argtypes = [wintypes.HMODULE, wintypes.LPVOID, wintypes.LPVOID, LANGS, wintypes.LPARAM]


def _check(ok, what: str):
    if not ok:
        raise OSError(f"{what} failed: {ctypes.WinError(ctypes.get_last_error())}")
    return ok


def _existing(exe: Path, kind: int) -> list[tuple[object, int]]:
    """(name, language) of every resource of `kind`: names are integer ids (or strings, kept as such)."""
    module = _check(kernel32.LoadLibraryExW(str(exe), None, LOAD_LIBRARY_AS_DATAFILE), "LoadLibraryEx")
    found: list[tuple[object, int]] = []
    try:
        names: list[object] = []

        def on_name(_module, _kind, name, _param):
            names.append(name if name >> 16 == 0 else ctypes.wstring_at(name))   # MAKEINTRESOURCE or a string
            return True

        kernel32.EnumResourceNamesW(module, kind, NAMES(on_name), 0)
        for name in names:
            def on_language(_module, _kind, _name, language, _param, name=name):
                found.append((name, language))
                return True

            key = name if isinstance(name, int) else ctypes.c_wchar_p(name)
            kernel32.EnumResourceLanguagesW(module, kind, key, LANGS(on_language), 0)
    finally:
        kernel32.FreeLibrary(module)
    return found


def _resource_key(name):
    return name if isinstance(name, int) else ctypes.c_wchar_p(name)


def set_icon(exe: Path, ico: Path) -> None:
    data = ico.read_bytes()
    _, kind, count = struct.unpack_from("<HHH", data, 0)
    if kind != 1:
        raise ValueError(f"{ico} isn't an icon file")
    images = []
    for index in range(count):
        width, height, colours, _, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
        images.append(((width, height, colours, planes, bits, size), data[offset:offset + size]))
    groups = _existing(exe, RT_GROUP_ICON)
    icons = _existing(exe, RT_ICON)
    language = groups[0][1] if groups else 0x0409
    handle = _check(kernel32.BeginUpdateResourceW(str(exe), False), "BeginUpdateResource")
    try:
        for name, lang in icons:
            kernel32.UpdateResourceW(handle, RT_ICON, _resource_key(name), lang, None, 0)
        for name, lang in groups:
            kernel32.UpdateResourceW(handle, RT_GROUP_ICON, _resource_key(name), lang, None, 0)
        directory = struct.pack("<HHH", 0, 1, len(images))
        for number, ((width, height, colours, planes, bits, size), image) in enumerate(images, 1):
            buffer = ctypes.create_string_buffer(image, len(image))
            _check(kernel32.UpdateResourceW(handle, RT_ICON, number, language, buffer, len(image)), "UpdateResource")
            directory += struct.pack("<BBBBHHIH", width, height, colours, 0, planes, bits, size, number)
        buffer = ctypes.create_string_buffer(directory, len(directory))
        group = groups[0][0] if groups else 1
        _check(kernel32.UpdateResourceW(handle, RT_GROUP_ICON, _resource_key(group), language, buffer, len(directory)),
               "UpdateResource")
    except BaseException:
        kernel32.EndUpdateResourceW(handle, True)   # discard
        raise
    _check(kernel32.EndUpdateResourceW(handle, False), "EndUpdateResource")


def _block(key: str, value: bytes = b"", text: bool = True, children: bytes = b"") -> bytes:
    """One VS_VERSIONINFO-style block: length, value length, type, key (UTF-16, NUL), padding, value, children."""
    header_key = key.encode("utf-16-le") + b"\0\0"
    head = 6 + len(header_key)
    head += (4 - head % 4) % 4
    value_padded = value + b"\0" * ((4 - len(value) % 4) % 4)
    length = head + len(value_padded) + len(children)
    value_length = len(value) // 2 if text else len(value)
    out = struct.pack("<HHH", length, value_length, 1 if text else 0) + header_key
    out += b"\0" * (head - 6 - len(header_key))
    return out + value_padded + children


def _version_info(name: str, version: str, description: str, copyright_: str) -> bytes:
    numbers = [int(n) for n in (version.split(".") + ["0", "0", "0"])[:4]]
    ms, ls = numbers[0] << 16 | numbers[1], numbers[2] << 16 | numbers[3]
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x00010000, ms, ls, ms, ls, 0x3F, 0, 0x40004, 1, 0, 0, 0)
    strings = {"CompanyName": name, "FileDescription": description, "FileVersion": version,
               "InternalName": name, "LegalCopyright": copyright_, "OriginalFilename": f"{name}.exe",
               "ProductName": name, "ProductVersion": version}
    table = b"".join(_block(k, (v + "\0").encode("utf-16-le")) for k, v in strings.items())
    string_info = _block("StringFileInfo", children=_block("040904B0", children=table))
    var_info = _block("VarFileInfo", children=_block("Translation", struct.pack("<HH", 0x0409, 1200), text=False))
    return _block("VS_VERSION_INFO", fixed, text=False, children=string_info + var_info)


def set_version(exe: Path, name: str, version: str, description: str, copyright_: str = "") -> None:
    existing = _existing(exe, RT_VERSION)
    data = _version_info(name, version, description, copyright_)
    handle = _check(kernel32.BeginUpdateResourceW(str(exe), False), "BeginUpdateResource")
    try:
        for res_name, lang in existing:
            kernel32.UpdateResourceW(handle, RT_VERSION, _resource_key(res_name), lang, None, 0)
        buffer = ctypes.create_string_buffer(data, len(data))
        _check(kernel32.UpdateResourceW(handle, RT_VERSION, 1, 0x0409, buffer, len(data)), "UpdateResource")
    except BaseException:
        kernel32.EndUpdateResourceW(handle, True)
        raise
    _check(kernel32.EndUpdateResourceW(handle, False), "EndUpdateResource")
