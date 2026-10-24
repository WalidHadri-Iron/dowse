"""Builds Dowse's Windows installer: dist/Dowse-Setup-<version>.exe (Inno Setup 6, packaging/dowse.iss).

    uv run python scripts/build_installer.py [--skip-iscc]      (on Windows)

The app it installs is self-contained: Python's official embeddable package (no Python needed on the PC), Dowse
and its locked dependencies (uv.lock) beside it, the Visual C++ runtime the llama.cpp builds need (`runtime.REDIST`),
the icon. Its program is Dowse.exe: Python's pythonw.exe with Dowse's icon and name, so Task Manager, the taskbar
and Explorer's menu show Dowse. The installer also carries Microsoft's WebView2 bootstrapper, run only on a PC
without WebView2. What Dowse downloads on first start (the llama.cpp build for the PC's hardware, the model) isn't
in the installer: it depends on the PC, and setup fetches it with resume and verification.

Before: build the interface (`cd web && npm run build`). Downloads (embeddable Python, the WebView2 bootstrapper, Inno
Setup if it isn't installed) are cached in build/cache; Inno Setup, if needed, is installed for the current user in
%LOCALAPPDATA%\DowseBuild.
"""

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exe_resources  # noqa: E402
from buildtools import download, say, uv, version  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "installer"
CACHE = ROOT / "build" / "cache"
STAGE = BUILD / "app"
DIST = ROOT / "dist"

PYTHON = "3.12.10"   # the version the project is developed and tested on (its venv: uv's 3.12)
PYTHON_ZIP = f"python-{PYTHON}-embed-amd64.zip"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON}/{PYTHON_ZIP}"
PYTHON_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"   # the Evergreen bootstrapper (~2 MB)
INNO_URL = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe"
INNO_SHA256 = "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732"
REDIST = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")


def stage(app_version: str) -> None:
    if not (ROOT / "src" / "dowse" / "desktop" / "static" / "index.html").exists():
        raise SystemExit("The interface isn't built: cd web && npm run build")
    shutil.rmtree(BUILD, ignore_errors=True)
    STAGE.mkdir(parents=True)

    say("Python (embeddable)…")
    archive = download(PYTHON_URL, CACHE / PYTHON_ZIP, PYTHON_SHA256)
    with zipfile.ZipFile(archive) as zip_file:
        zip_file.extractall(STAGE)
    tag = "python" + "".join(PYTHON.split(".")[:2])
    # The ._pth file makes this Python ignore the PC's own Python and environment variables: only its own folder,
    # its standard library and the app's libraries. `import site` lets packages' .pth files work.
    (STAGE / f"{tag}._pth").write_text(f"{tag}.zip\n.\nLib\\site-packages\nimport site\n", encoding="ascii")
    shutil.copy2(STAGE / "pythonw.exe", STAGE / "Dowse.exe")
    exe_resources.set_icon(STAGE / "Dowse.exe", ROOT / "assets" / "brand" / "dowse.ico")
    exe_resources.set_version(STAGE / "Dowse.exe", "Dowse", app_version, "Dowse")

    say("Dowse and its libraries (uv.lock)…")
    wheels = BUILD / "wheels"
    uv("build", "--wheel", "--out-dir", str(wheels))
    requirements = BUILD / "requirements.txt"
    uv("export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes", "--format", "requirements-txt",
       "-o", str(requirements))
    site = STAGE / "Lib" / "site-packages"
    # Resolved and built (a few pure-Python packages ship only as source) with this Python, the same 3.12 on the same
    # platform: the embeddable one can't build packages (no venv).
    common = ["--target", str(site), "--python", sys.executable, "--python-platform", "x86_64-pc-windows-msvc",
              "--no-cache"]
    uv("pip", "install", *common, "-r", str(requirements))
    wheel = next(wheels.glob(f"dowse-{app_version}-*.whl"))
    uv("pip", "install", *common, "--no-deps", str(wheel))

    say("Compiling the libraries (a faster first start)…")
    subprocess.run([str(STAGE / "python.exe"), "-m", "compileall", "-q", "-j", "0", str(site)], check=False)

    say("The Visual C++ runtime, the icon, the command line…")
    (STAGE / "redist").mkdir()
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for name in REDIST:
        if not (system / name).exists():
            raise SystemExit(f"{name} isn't in {system}: install the Visual C++ Redistributable (x64) on this PC")
        shutil.copy2(system / name, STAGE / "redist" / name)
    shutil.copy2(ROOT / "assets" / "brand" / "dowse.ico", STAGE / "dowse.ico")
    (STAGE / "bin").mkdir()
    (STAGE / "bin" / "dowse.cmd").write_text('@"%~dp0..\\python.exe" -m dowse.cli %*\r\n', encoding="ascii")

    say("Checking the staged app…")
    check = subprocess.run([str(STAGE / "python.exe"), "-c", (
        "import sqlite3, dowse, dowse.desktop.app, webview, pystray, av, numpy, pypdfium2;"
        "db = sqlite3.connect(':memory:'); db.execute('CREATE VIRTUAL TABLE t USING fts5(x)');"
        "print(dowse.__version__)")], capture_output=True, text=True)
    if check.returncode != 0 or check.stdout.strip() != app_version:
        raise SystemExit(f"The staged app doesn't start:\n{check.stdout}\n{check.stderr}")


def iscc() -> Path:
    """Inno Setup's compiler: an installed one, else one installed for this user into %LOCALAPPDATA%\\DowseBuild
    (on C:, where its setup can install: the build cache may be on a network share)."""
    tools = Path(os.environ.get("LOCALAPPDATA", "")) / "DowseBuild" / "innosetup"
    candidates = [Path(os.environ.get("ISCC", "")), tools / "ISCC.exe",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
                  Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
                  Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe"]
    for candidate in candidates:
        if candidate.name == "ISCC.exe" and candidate.exists():
            return candidate
    say(f"Inno Setup (into {tools}, for this user)…")
    setup = download(INNO_URL, CACHE / Path(INNO_URL).name, INNO_SHA256)
    tools.parent.mkdir(parents=True, exist_ok=True)
    local = Path(shutil.copy2(setup, tools.parent / setup.name))   # run from C: (a share's file may prompt first)
    subprocess.run([str(local), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/CURRENTUSER",
                    f"/DIR={tools}", "/NOICONS"], check=True)
    return tools / "ISCC.exe"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-iscc", action="store_true", help="stage the app only (build/installer/app)")
    args = parser.parse_args()
    app_version = version()
    say(f"Dowse {app_version}")
    stage(app_version)
    if args.skip_iscc:
        return
    webview2 = download(WEBVIEW2_URL, CACHE / "MicrosoftEdgeWebview2Setup.exe")
    DIST.mkdir(exist_ok=True)
    say("The installer…")
    subprocess.run([str(iscc()), f"/DAppVersion={app_version}", f"/DSourceDir={STAGE}", f"/DOutputDir={DIST}",
                    f"/DWebView2Setup={webview2}", f"/DSetupIcon={ROOT / 'assets' / 'brand' / 'dowse.ico'}",
                    "/Q", str(ROOT / "packaging" / "dowse.iss")], check=True)
    output = DIST / f"Dowse-Setup-{app_version}.exe"
    say(f"{output} ({output.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
