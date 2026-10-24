"""Builds Dowse for a Mac (Apple Silicon): dist/Dowse.app and dist/Dowse-<version>.dmg.

    uv run python scripts/build_mac_app.py [--no-dmg]      (on a Mac with Xcode's command line tools)

The app is self-contained: a relocatable Python (python-build-standalone, the build uv itself installs) in
Contents/Resources/python, with Dowse and its locked dependencies (uv.lock) in its site-packages. Its program,
Contents/MacOS/Dowse, is packaging/macos/launcher.c: that Python embedded, so macOS names the app, its menu and its
Dock icon Dowse. What Dowse downloads on first start (the llama.cpp build, the model) isn't in the app, as on Windows.

Signing: with no identity, the app is signed ad hoc (Apple Silicon runs nothing unsigned), and macOS asks before it
opens one downloaded (System Settings → Privacy & Security → Open Anyway). With a Developer ID it's signed for
distribution (the hardened runtime, packaging/macos/entitlements.plist) and, given notary credentials, notarized and
stapled, so it opens like any app:

    DOWSE_MAC_IDENTITY="Developer ID Application: <name> (<team>)"   the signing identity, in the keychain
    DOWSE_NOTARY_PROFILE=<profile>       credentials saved with `xcrun notarytool store-credentials <profile>`

Before: build the interface (`cd web && npm run build`). The Python download is cached in build/cache.
"""

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from buildtools import ROOT, download, say, uv, version  # noqa: E402

BUILD = ROOT / "build" / "mac"
CACHE = ROOT / "build" / "cache"
DIST = ROOT / "dist"
PACKAGING = ROOT / "packaging" / "macos"

PYTHON = "3.12.15"
PYTHON_RELEASE = "20261003"
PYTHON_TAR = f"cpython-{PYTHON}+{PYTHON_RELEASE}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PYTHON_URL = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_RELEASE}/{PYTHON_TAR}"
PYTHON_SHA256 = "ad8d0c637c0a36b967b310e2c07254f4d2ca8cabaa7699e55ed6290aceb481a2"
TAG = "python" + ".".join(PYTHON.split(".")[:2])   # python3.12
BUNDLE_ID = "app.dowse.Dowse"
MINIMUM_MACOS = "12.0"


def run(*command: str | Path, **options) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in command], check=True, **options)


def info_plist(app_version: str) -> dict:
    return {
        "CFBundleName": "Dowse", "CFBundleDisplayName": "Dowse", "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "Dowse", "CFBundleIconFile": "Dowse", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": app_version, "CFBundleVersion": app_version,
        "CFBundleInfoDictionaryVersion": "6.0", "LSMinimumSystemVersion": MINIMUM_MACOS,
        "LSApplicationCategoryType": "public.app-category.productivity", "NSHighResolutionCapable": True,
        # A menu-bar app: no Dock icon at rest. Its window's process becomes a regular app (pywebview), with one.
        "LSUIElement": True,
        "NSHumanReadableCopyright": "Free and open source (MIT License).",
    }


def stage(app_version: str) -> Path:
    if not (ROOT / "src" / "dowse" / "desktop" / "static" / "index.html").exists():
        raise SystemExit("The interface isn't built: cd web && npm run build")
    shutil.rmtree(BUILD, ignore_errors=True)
    app = BUILD / "Dowse.app"
    contents = app / "Contents"
    resources = contents / "Resources"
    (contents / "MacOS").mkdir(parents=True)
    resources.mkdir()

    say("Python (python-build-standalone)…")
    archive = download(PYTHON_URL, CACHE / PYTHON_TAR, PYTHON_SHA256)
    with tarfile.open(archive) as tar:   # one top folder: python/
        tar.extractall(resources, filter="data")
    home = resources / "python"
    python = home / "bin" / "python3"
    site = home / "lib" / TAG / "site-packages"
    # The app's Python is the app's own: no installer marker, no pip, no Tk (Dowse has no Tk windows)
    (home / "lib" / TAG / "EXTERNALLY-MANAGED").unlink(missing_ok=True)
    for unused in ("idlelib", "tkinter", "turtledemo", "ensurepip", "test"):
        shutil.rmtree(home / "lib" / TAG / unused, ignore_errors=True)
    for pip in site.glob("pip*"):
        shutil.rmtree(pip, ignore_errors=True)

    say("Dowse and its libraries (uv.lock)…")
    wheels = BUILD / "wheels"
    uv("build", "--wheel", "--out-dir", str(wheels))
    requirements = BUILD / "requirements.txt"
    uv("export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes", "--format", "requirements-txt",
       "-o", str(requirements))
    common = ["--target", str(site), "--python", str(python), "--python-platform", "aarch64-apple-darwin",
              "--no-cache"]
    uv("pip", "install", *common, "-r", str(requirements))
    wheel = next(wheels.glob(f"dowse-{app_version}-*.whl"))
    uv("pip", "install", *common, "--no-deps", str(wheel))

    say("Compiling the libraries (a faster first start)…")
    subprocess.run([str(python), "-m", "compileall", "-q", "-j", "0", str(home / "lib" / TAG)], check=False)

    say("The program (packaging/macos/launcher.c), the icon, Info.plist…")
    program = contents / "MacOS" / "Dowse"
    library = home / "lib" / f"lib{TAG}.dylib"
    run("clang", "-O2", "-arch", "arm64", f"-mmacosx-version-min={MINIMUM_MACOS}",
        f"-I{home / 'include' / TAG}", PACKAGING / "launcher.c", "-o", program,
        f"-L{home / 'lib'}", f"-l{TAG}", "-Wl,-rpath,@executable_path/../Resources/python/lib")
    # Python's library may name itself by its build machine's path: the program finds it inside the app instead
    named = run("otool", "-D", library, capture_output=True, text=True).stdout.splitlines()[-1].strip()
    if named != f"@rpath/{library.name}":
        run("install_name_tool", "-change", named, f"@rpath/{library.name}", program)
    shutil.copy2(ROOT / "assets" / "brand" / "dowse.icns", resources / "Dowse.icns")
    (contents / "Info.plist").write_bytes(plistlib.dumps(info_plist(app_version)))
    (contents / "PkgInfo").write_text("APPL????", encoding="ascii")
    return app


def sign(app: Path) -> None:
    """Every piece of code inside, then the app: with the Developer ID (hardened runtime, a secure timestamp), or ad
    hoc. Inside out, as codesign requires (--deep is for checking, not signing)."""
    identity = os.environ.get("DOWSE_MAC_IDENTITY", "")
    say(f"Signing ({identity or 'ad hoc'})…")
    flags = ["--force", "--sign", identity or "-"]
    if identity:
        flags += ["--options", "runtime", "--timestamp", "--entitlements", str(PACKAGING / "entitlements.plist")]
    code = [p for p in (app / "Contents" / "Resources").rglob("*") if p.is_file() and not p.is_symlink()
            and (p.suffix in (".so", ".dylib") or os.access(p, os.X_OK) and _is_macho(p))]
    for start in range(0, len(code), 200):
        run("codesign", *flags, *code[start:start + 200])
    run("codesign", *flags, app)
    run("codesign", "--verify", "--deep", "--strict", app)


def _is_macho(path: Path) -> bool:
    with path.open("rb") as file:
        return file.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")   # 64-bit Mach-O, universal


def check(app: Path, app_version: str) -> None:
    """The app's own program, as Python: the libraries load, from inside the app, and nothing from the Mac's Python."""
    say("Checking the app…")
    result = subprocess.run([str(app / "Contents" / "MacOS" / "Dowse"), "-c", (
        "import sys, sqlite3, dowse, dowse.desktop.app, webview, pystray, av, numpy, pypdfium2, AppKit, Vision;"
        "db = sqlite3.connect(':memory:'); db.execute('CREATE VIRTUAL TABLE t USING fts5(x)');"
        "assert sys.prefix.endswith('/Dowse.app/Contents/Resources/python'), sys.prefix;"
        "assert sys.executable.endswith('/Dowse.app/Contents/MacOS/Dowse'), sys.executable;"
        "print(dowse.__version__)")], capture_output=True, text=True, env={"HOME": os.environ["HOME"]})
    if result.returncode != 0 or result.stdout.strip() != app_version:
        raise SystemExit(f"The app doesn't start:\n{result.stdout}\n{result.stderr}")


def dmg(app: Path, app_version: str) -> Path:
    """A disk image to drag Dowse into Applications from."""
    say("The disk image…")
    folder = BUILD / "dmg"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir()
    run("ditto", app, folder / "Dowse.app")
    (folder / "Applications").symlink_to("/Applications")
    output = DIST / f"Dowse-{app_version}.dmg"
    output.unlink(missing_ok=True)
    run("hdiutil", "create", "-volname", "Dowse", "-srcfolder", folder, "-fs", "HFS+", "-format", "ULFO", "-ov",
        output)
    if identity := os.environ.get("DOWSE_MAC_IDENTITY"):
        run("codesign", "--force", "--sign", identity, "--timestamp", output)
    if profile := os.environ.get("DOWSE_NOTARY_PROFILE"):
        say("Notarizing (Apple's notary service)…")
        run("xcrun", "notarytool", "submit", output, "--keychain-profile", profile, "--wait")
        run("xcrun", "stapler", "staple", output)
    return output


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Dowse.app is built on a Mac")
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-dmg", action="store_true", help="build dist/Dowse.app only")
    args = parser.parse_args()
    app_version = version()
    say(f"Dowse {app_version}")
    app = stage(app_version)
    sign(app)
    check(app, app_version)
    DIST.mkdir(exist_ok=True)
    shutil.rmtree(DIST / "Dowse.app", ignore_errors=True)
    run("ditto", app, DIST / "Dowse.app")
    say(f"{DIST / 'Dowse.app'}")
    if not args.no_dmg:
        output = dmg(app, app_version)
        say(f"{output} ({output.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
