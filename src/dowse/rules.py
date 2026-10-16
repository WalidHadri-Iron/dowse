"""What gets indexed, and what never does."""

import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath


class Kind(StrEnum):
    SCREENSHOT = "screenshot"
    IMAGE = "image"
    PDF = "pdf"
    DOC = "doc"
    AUDIO = "audio"
    VIDEO = "video"


# The first pass reads what people look for most first.
ORDER = {Kind.SCREENSHOT: 0, Kind.IMAGE: 1, Kind.PDF: 2, Kind.DOC: 3, Kind.AUDIO: 4, Kind.VIDEO: 5}

IMAGE_EXTENSIONS = frozenset("jpg jpeg jpe jfif png gif bmp webp tif tiff heic heif avif".split())
DOC_EXTENSIONS = frozenset("txt text md markdown rst rtf docx odt odp ods html htm pptx xlsx eml epub csv tsv".split()
                           # source code outside code projects (a project's own files are left out: PROJECT_MARKERS)
                           + "py js ts jsx tsx java c h cpp hpp cs go rs rb php sh ps1 bat sql swift kt lua r".split())
AUDIO_EXTENSIONS = frozenset("mp3 wav m4a aac flac ogg oga opus wma".split())
VIDEO_EXTENSIONS = frozenset("mp4 m4v mov mkv avi webm wmv mpg mpeg".split())

# Folders never worth walking into, wherever they are.
SKIPPED_FOLDERS = frozenset(n.lower() for n in [
    "node_modules", "bower_components", "venv", ".venv", "site-packages", "__pycache__", ".git", ".svn", ".hg",
    "$recycle.bin", "system volume information", "windows", "program files", "program files (x86)", "programdata",
    "appdata", "msocache", "$windows.~bt", "$windows.~ws", "windowsapps", "config.msi", ".cache", ".vs", ".idea",
    ".gradle", ".m2", ".nuget",
])

# Any of these in a folder makes it a code project, where only screenshots are indexed.
PROJECT_MARKERS = frozenset([
    ".git", "package.json", "pyproject.toml", "setup.py", "requirements.txt", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "CMakeLists.txt", "Package.swift",
])
PROJECT_EXTENSIONS = frozenset(["sln", "csproj", "vcxproj", "fsproj", "xcodeproj"])

SCREENSHOT_PREFIXES = ("screenshot", "screen shot", "capture", "snip", "cleanshot", "scr-", "sharex")
SCREENSHOT_FOLDERS = frozenset(["screenshots", "screen captures"])

SECRET_EXTENSIONS = frozenset("pem key p12 pfx keystore jks ppk kdbx gpg asc crt cer der p8".split())
SECRET_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".env", "secret", "credential", ".netrc", ".npmrc",
                   ".pypirc", "htpasswd", ".htpasswd")

# Windows file attributes (stat.FILE_ATTRIBUTE_*), some missing from older Pythons.
_HIDDEN_OR_SYSTEM = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM
# OneDrive/Dropbox "online-only" files: reading one downloads it. Never touched.
_CLOUD_ONLY = getattr(stat, "FILE_ATTRIBUTE_OFFLINE", 0x1000) | 0x40000 | 0x400000   # RECALL_ON_OPEN, ..._DATA_ACCESS


def extension(name: str) -> str:
    return PurePath(name).suffix.lower().lstrip(".")


def is_secret(name: str) -> bool:
    lower = name.lower()
    return extension(lower) in SECRET_EXTENSIONS or lower.startswith(SECRET_PREFIXES)


def is_screenshot(path: str) -> bool:
    pure = PurePath(path)
    return pure.name.lower().startswith(SCREENSHOT_PREFIXES) or pure.parent.name.lower() in SCREENSHOT_FOLDERS


SKIPPED_TYPE = "a type you chose to skip"


@dataclass(frozen=True)
class Skip:
    """The file types the user chose not to search: whole kinds (screenshots, videos…) and extensions ("txt")."""
    kinds: frozenset[Kind] = frozenset()
    extensions: frozenset[str] = frozenset()   # lowercase, without the dot

    @classmethod
    def of(cls, kinds=(), extensions=()) -> "Skip":
        return cls(frozenset(Kind(k) for k in kinds),
                   frozenset(e.strip().lower().lstrip("*.") for e in extensions if e.strip().lstrip("*.")))

    @classmethod
    def from_settings(cls, settings: dict) -> "Skip":
        return cls.of(settings.get("skip_kinds", []), settings.get("skip_extensions", []))

    def to_settings(self) -> dict:
        return {"skip_kinds": sorted(k.value for k in self.kinds), "skip_extensions": sorted(self.extensions)}


NOTHING = Skip()


def classify(path: str, skip: Skip = NOTHING) -> Kind | str | None:
    """A file's kind, a reason it's skipped (str), or None for a type Dowse doesn't read."""
    name = PurePath(path).name
    ext = extension(name)
    if name.startswith("~$"):   # Office's lock file beside an open document
        return None
    if ext in skip.extensions:
        return SKIPPED_TYPE
    if is_secret(name):
        return "key or password file"
    kind = None
    if ext in IMAGE_EXTENSIONS:
        kind = Kind.SCREENSHOT if is_screenshot(path) else Kind.IMAGE
    elif ext == "pdf":
        kind = Kind.PDF
    elif ext in DOC_EXTENSIONS:
        kind = Kind.DOC
    elif ext in AUDIO_EXTENSIONS:
        kind = Kind.AUDIO
    elif ext in VIDEO_EXTENSIONS:
        kind = Kind.VIDEO
    return SKIPPED_TYPE if kind in skip.kinds else kind


def attributes(entry: os.DirEntry | str) -> int:
    """Windows file attributes (0 elsewhere, or when the file can't be looked at)."""
    try:
        info = entry.stat(follow_symlinks=False) if isinstance(entry, os.DirEntry) else os.stat(entry)
        return getattr(info, "st_file_attributes", 0)
    except OSError:
        return 0


def hidden(name: str, attrs: int) -> bool:
    return name.startswith(".") or bool(attrs & _HIDDEN_OR_SYSTEM)


def cloud_only(attrs: int) -> bool:
    return bool(attrs & _CLOUD_ONLY)


def folder_skip_reason(path: str, attrs: int, excluded: list[str]) -> str | None:
    """Why a folder isn't walked into, or None. (The same for a full crawl and for one changed file's folders.)"""
    name = PurePath(path).name
    lower = name.lower()
    if lower in SKIPPED_FOLDERS:
        return "system or developer folder"
    if hidden(name, attrs):
        return "hidden folder"
    if lower.endswith(".noindex"):
        return ".noindex folder"
    full = os.path.normcase(path)
    if any(full == os.path.normcase(e) or full.startswith(os.path.normcase(e).rstrip("\\/") + os.sep) for e in excluded):
        return "excluded folder"
    return None


def is_project_root(names: set[str]) -> bool:
    return bool(PROJECT_MARKERS & names) or any(extension(n) in PROJECT_EXTENSIONS for n in names)


CAMERA_PREFIXES = ("img", "dsc", "pxl", "mvimg", "vid", "gopr", "dji", "photo", "whatsapp", "screenshot", "wp_")


def looks_machine_named(stem: str) -> bool:
    """"clip_000123", "frame-0042" or "3f9a1c0e-…", but not camera names like "IMG_1234"."""
    name = stem.lower()
    if name.startswith(CAMERA_PREFIXES):
        return False
    if re.fullmatch(r"[0-9a-f]{8,}([-_][0-9a-f]{4,})*", name):
        return True
    return re.fullmatch(r"[a-z]{0,16}[-_. ]?\d{3,}([-_.]\d+)*", name) is not None


def dataset_folders(files: list[tuple[str, str, Kind]], roots: set[str], minimum: int = 300) -> dict[str, int]:
    """Folders with hundreds of same-kind, machine-named media files: training data, not your stuff. Skipped and
    reported. A root you picked yourself is never a dataset. `files`: (folder, stem, kind)."""
    groups: dict[tuple[str, Kind], list[str]] = {}
    for folder, stem, kind in files:
        if kind in (Kind.IMAGE, Kind.AUDIO, Kind.VIDEO) and folder not in roots:
            groups.setdefault((folder, kind), []).append(stem)
    found: dict[str, int] = {}
    for (folder, _), stems in groups.items():
        if len(stems) >= minimum and sum(map(looks_machine_named, stems)) / len(stems) >= 0.7:
            found[folder] = found.get(folder, 0) + len(stems)
    return found
