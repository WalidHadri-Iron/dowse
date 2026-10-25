"""Builds Dowse for Linux: dist/Dowse-<version>-<arch>.AppImage, one file that runs on most distributions (glibc 2.34
or later: Ubuntu 22.04, Debian 12, Fedora 35, RHEL 9 and since).

    uv run python scripts/build_linux_app.py [--no-appimage]      (on Linux)

The AppImage is self-contained: a relocatable Python (python-build-standalone, the build uv itself installs) in
usr/python, with Dowse and its locked dependencies (uv.lock) in its site-packages, Qt's WebEngine among them (the
window: Linux's own web view, WebKitGTK, can't be used from a Python of the app's own). Its entry point is
packaging/linux/AppRun. What Dowse downloads on first start (the llama.cpp build, the model) isn't in it, as on
Windows and macOS. Run from the AppImage, Dowse puts itself in the apps menu (`linuxapi.integrate`).

Before: build the interface (`cd web && npm run build`). Downloads (the Python, appimagetool and the AppImage runtime,
each pinned and checked) are cached in build/cache.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from buildtools import ROOT, download, say, uv, version  # noqa: E402

BUILD = ROOT / "build" / "linux"
CACHE = ROOT / "build" / "cache"
DIST = ROOT / "dist"
PACKAGING = ROOT / "packaging" / "linux"

ARCH = "aarch64" if platform.machine() in ("aarch64", "arm64") else "x86_64"
PYTHON = "3.12.15"
PYTHON_RELEASE = "20261003"
PYTHON_TAR = f"cpython-{PYTHON}+{PYTHON_RELEASE}-{ARCH}-unknown-linux-gnu-install_only_stripped.tar.gz"
PYTHON_URL = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_RELEASE}/{PYTHON_TAR}"
PYTHON_SHA256 = {"x86_64": "731af898886c5f821890dc901eca3c651cca8e51fa7308c159d12a1194aeac91",
                 "aarch64": "6541297dd1798dec8b98c3ad7492808a5b9d1c126801ceb2011e7754cd20d1ce"}[ARCH]
TAG = "python" + ".".join(PYTHON.split(".")[:2])   # python3.12
WHEELS = f"{ARCH}-manylinux_2_34"   # PySide6's wheels need glibc 2.34: the oldest systems the AppImage runs on

# Qt as Dowse uses it: its window (QtWebEngineWidgets, through pywebview) and what that loads. PySide6's wheels hold all
# of Qt (3D, charts, multimedia with an FFmpeg of its own, the designer, QML and its tools), ~800 MB unpacked that no
# Dowse window opens; what's kept is found from the libraries these need (`_needed`), not listed by hand.
QT_MODULES = ("QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtDBus", "QtOpenGL", "QtPrintSupport", "QtSvg",
              "QtWebChannel", "QtWebEngineCore", "QtWebEngineWidgets")
QT_PLUGINS = ("platforms", "platformthemes", "platforminputcontexts", "xcbglintegrations", "wayland-decoration-client",
              "wayland-graphics-integration-client", "wayland-shell-integration", "imageformats", "iconengines", "tls",
              "networkinformation", "printsupport")
UNUSED_PLUGINS = ("libqeglfs.so", "libqlinuxfb.so", "libqminimalegl.so", "libqvkkhrdisplay.so", "libqvnc.so",   # embedded
                  "libqtvirtualkeyboardplugin.so", "libqpdf.so")
PYSIDE_TOOLS = ("assistant", "balsam", "balsamui", "designer", "linguist", "lrelease", "lupdate", "qmlformat", "qmllint",
                "qmlls", "qsb", "svgtoqml", "include", "doc", "glue", "typesystems", "scripts")
# Libraries Qt's X11 plugin needs that a desktop may lack (Ubuntu's doesn't install libxcb-cursor): carried beside Qt's
# own libraries, where its plugins look (their RUNPATH), from the machine that builds the AppImage, when it has them.
# (Not through LD_LIBRARY_PATH: everything Dowse starts, xdg-open, a browser, would inherit it.)
X11_LIBRARIES = ("libxcb-cursor.so.0", "libxcb-icccm.so.4", "libxcb-image.so.0", "libxcb-keysyms.so.1",
                 "libxcb-render-util.so.0", "libxcb-util.so.1", "libxcb-xkb.so.1", "libxkbcommon-x11.so.0")

APPIMAGETOOL = f"https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-{ARCH}.AppImage"
APPIMAGETOOL_SHA256 = {"x86_64": "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"}.get(ARCH)
RUNTIME = f"https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-{ARCH}"
RUNTIME_SHA256 = {"x86_64": "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d",
                  "aarch64": "00cbdfcf917cc6c0ff6d3347d59e0ca1f7f45a6df1a428a0d6d8a78664d87444"}[ARCH]


def stage(app_version: str) -> Path:
    if not (ROOT / "src" / "dowse" / "desktop" / "static" / "index.html").exists():
        raise SystemExit("The interface isn't built: cd web && npm run build")
    shutil.rmtree(BUILD, ignore_errors=True)
    app = BUILD / "Dowse.AppDir"
    (app / "usr").mkdir(parents=True)

    say("Python (python-build-standalone)…")
    archive = download(PYTHON_URL, CACHE / PYTHON_TAR, PYTHON_SHA256)
    with tarfile.open(archive) as tar:   # one top folder: python/
        tar.extractall(app / "usr", filter="data")
    home = app / "usr" / "python"
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
    common = ["--target", str(site), "--python", str(python), "--python-platform", WHEELS, "--no-cache"]
    uv("pip", "install", *common, "-r", str(requirements))
    wheel = next(wheels.glob(f"dowse-{app_version}-*.whl"))
    uv("pip", "install", *common, "--no-deps", str(wheel))

    say("Qt, as Dowse uses it…")
    prune_qt(site / "PySide6")
    shutil.rmtree(site / "bin", ignore_errors=True)   # the packages' commands: AppRun is the AppImage's
    say("The X11 libraries a desktop may lack…")
    carry_x11(site / "PySide6" / "Qt" / "lib")

    say("Compiling the libraries (a faster first start)…")
    subprocess.run([str(python), "-m", "compileall", "-q", "-j", "0", str(home / "lib" / TAG)], check=False)

    say("AppRun, the desktop entry, the icon…")
    shutil.copy2(PACKAGING / "AppRun", app / "AppRun")
    (app / "AppRun").chmod(0o755)
    shutil.copy2(PACKAGING / "dowse.desktop", app / "dowse.desktop")
    icon = ROOT / "src" / "dowse" / "desktop" / "dowse.png"
    shutil.copy2(icon, app / "dowse.png")
    themed = app / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    themed.mkdir(parents=True)
    shutil.copy2(icon, themed / "dowse.png")
    (app / ".DirIcon").symlink_to("dowse.png")
    return app


def _needed(binary: Path) -> list[str]:
    """The libraries `binary` names as needed (readelf: what's written in it, whatever this machine has)."""
    out = subprocess.run(["readelf", "-d", str(binary)], capture_output=True, text=True).stdout
    return [line.split("[", 1)[1].rstrip("]") for line in out.splitlines() if "(NEEDED)" in line]


def prune_qt(pyside: Path) -> None:
    qt = pyside / "Qt"
    for module in pyside.glob("Qt*.abi3.so"):
        if module.name.split(".")[0] not in QT_MODULES:
            module.unlink()
            module.with_name(module.name.split(".")[0] + ".pyi").unlink(missing_ok=True)
    for tool in PYSIDE_TOOLS:
        path = pyside / tool
        shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
    for folder in qt.glob("plugins/*"):
        if folder.name not in QT_PLUGINS:
            shutil.rmtree(folder)
    for plugin in UNUSED_PLUGINS:
        for found in qt.glob(f"plugins/*/{plugin}"):
            found.unlink()
    for folder in ("qml", "metatypes"):
        shutil.rmtree(qt / folder, ignore_errors=True)
    for tool in (qt / "libexec").iterdir():
        if tool.name != "QtWebEngineProcess":
            tool.unlink()
    # What all that needs, from Qt's own libraries
    libraries = {f.name: f for f in (qt / "lib").iterdir() if f.is_file()}
    pending = [*pyside.glob("*.so*"), qt / "libexec" / "QtWebEngineProcess", *qt.glob("plugins/*/*.so")]
    kept: set[str] = set()
    while pending:
        for name in _needed(pending.pop()):
            if name in libraries and name not in kept:
                kept.add(name)
                pending.append(libraries[name])
    for name, path in libraries.items():
        if name not in kept and not any(name.startswith(k.split(".so")[0] + ".so") for k in kept):
            path.unlink()


def carry_x11(into: Path) -> None:
    system = [Path(f"/usr/lib/{ARCH}-linux-gnu"), Path("/usr/lib64"), Path("/usr/lib")]
    missing = []
    for name in X11_LIBRARIES:
        found = next((folder / name for folder in system if (folder / name).exists()), None)
        if found is None:
            missing.append(name)
            continue
        shutil.copy2(found.resolve(), into / name)
    if missing:
        say(f"  not on this machine, so not carried: {', '.join(missing)}")


def check(app: Path, app_version: str) -> None:
    """The app's Python, as the AppImage starts it: the libraries load, from inside, Qt's WebEngine among them."""
    say("Checking the app…")
    result = subprocess.run([str(app / "AppRun"), "cli", "--version"], capture_output=True, text=True,
                            env={"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin"})
    imports = subprocess.run([str(app / "usr" / "python" / "bin" / "python3"), "-c", (
        "import sys, sqlite3, dowse, dowse.desktop.app, dowse.desktop.windows, webview, av, numpy, pypdfium2;"
        "from PySide6 import QtWebEngineWidgets;"
        "db = sqlite3.connect(':memory:'); db.execute('CREATE VIRTUAL TABLE t USING fts5(x)');"
        "assert sys.prefix.endswith('/Dowse.AppDir/usr/python'), sys.prefix;"
        "print(dowse.__version__)")], capture_output=True, text=True,
        env={"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin", "QT_QPA_PLATFORM": "offscreen"})
    if imports.returncode != 0 or imports.stdout.strip() != app_version or app_version not in result.stdout:
        raise SystemExit(f"The app doesn't start:\n{result.stdout}{result.stderr}\n{imports.stdout}\n{imports.stderr}")


def appimage(app: Path, app_version: str) -> Path:
    say("The AppImage…")
    if APPIMAGETOOL_SHA256 is None:
        raise SystemExit(f"No pinned appimagetool for {ARCH} yet")
    tool = download(APPIMAGETOOL, CACHE / f"appimagetool-1.9.1-{ARCH}.AppImage", APPIMAGETOOL_SHA256)
    tool.chmod(0o755)
    runtime = download(RUNTIME, CACHE / f"type2-runtime-20251108-{ARCH}", RUNTIME_SHA256)
    output = DIST / f"Dowse-{app_version}-{ARCH}.AppImage"
    output.unlink(missing_ok=True)
    # Without FUSE where it's built (a container, CI), appimagetool runs from its own files unpacked
    subprocess.run([str(tool), "--runtime-file", str(runtime), "--no-appstream", str(app), str(output)],
                   check=True, env=os.environ | {"ARCH": ARCH, "APPIMAGE_EXTRACT_AND_RUN": "1"})
    return output


def main() -> None:
    if not sys.platform.startswith("linux"):
        raise SystemExit("Dowse's AppImage is built on Linux")
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-appimage", action="store_true", help="build build/linux/Dowse.AppDir only")
    args = parser.parse_args()
    app_version = version()
    say(f"Dowse {app_version} ({ARCH})")
    app = stage(app_version)
    check(app, app_version)
    say(f"{app} ({sum(f.stat().st_size for f in app.rglob('*') if f.is_file() and not f.is_symlink()) / 1e6:.0f} MB)")
    if not args.no_appimage:
        DIST.mkdir(exist_ok=True)
        output = appimage(app, app_version)
        say(f"{output} ({output.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
