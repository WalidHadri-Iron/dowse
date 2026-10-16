"""Installing on any PC: downloads that recover from being interrupted, the graphics card Windows reports (not one
assumed) and the processor when a card can't run the model, the Visual C++ runtime beside each llama.cpp build,
the installer's way to close a running app, and the installed program's own name and icon."""

import http.server
import json
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

from dowse import download, runtime
from dowse.desktop import launch, setup, winapi

DATA = bytes(range(256)) * 400   # 100 KB


@pytest.fixture
def served():
    """A local server for DATA that honours Range requests (as Hugging Face and GitHub do)."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            asked = self.headers.get("Range")
            if asked:
                start = int(asked.split("=")[1].split("-")[0])
                if start >= len(DATA):
                    self.send_response(416)
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Length", str(len(DATA) - start))
                self.end_headers()
                self.wfile.write(DATA[start:])
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(DATA)))
            self.end_headers()
            self.wfile.write(DATA)

        def log_message(self, *_):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/file"
    httpd.shutdown()


def test_an_interrupted_download_resumes_where_it_stopped(served, tmp_path):
    target = tmp_path / "model.gguf"
    target.with_name("model.gguf.part").write_bytes(DATA[:30_000])   # the connection dropped here
    seen = []
    download.fetch(served, target, size=len(DATA), checksum=download.hashlib.sha256(DATA).hexdigest(),
                   progress=lambda done, total: seen.append(done))
    assert target.read_bytes() == DATA
    assert seen[0] > 30_000   # it went on from there, not from 0


def test_a_partial_file_that_cannot_be_right_is_thrown_away(served, tmp_path):
    target = tmp_path / "model.gguf"
    part = target.with_name("model.gguf.part")
    part.write_bytes(DATA + b"extra")   # longer than the file: never resumable
    download.fetch(served, target, size=len(DATA))
    assert target.read_bytes() == DATA
    target.unlink()
    part.write_bytes(b"x" * len(DATA))   # complete but wrong: the checksum says so, and it goes
    with pytest.raises(OSError, match="checksum"):
        download.fetch(served, target, size=len(DATA), checksum=download.hashlib.sha256(DATA).hexdigest())
    assert not part.exists()
    download.fetch(served, target, size=len(DATA), checksum=download.hashlib.sha256(DATA).hexdigest())
    assert target.read_bytes() == DATA


def test_a_stale_partial_file_the_server_refuses_starts_again(served, tmp_path):
    target = tmp_path / "model.gguf"
    part = target.with_name("model.gguf.part")
    part.write_bytes(b"y" * len(DATA))   # the server says 416: nothing left to send, yet it isn't the file
    with pytest.raises(OSError):
        download.fetch(served, target, size=len(DATA) + 10)
    assert not part.exists()


def test_a_damaged_runtime_zip_is_fetched_again(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(url, to, progress=None, **_):
        calls.append(url)
        if len(calls) == 1:
            to.write_bytes(b"not a zip")
        else:
            import zipfile
            with zipfile.ZipFile(to, "w") as zip_file:
                zip_file.writestr(runtime.EXE, "server")
        return to

    monkeypatch.setattr(runtime, "fetch", fake_fetch)
    runtime._unpack("https://example/llama.zip", tmp_path / "llama.zip", tmp_path / "out", None)
    assert len(calls) == 2 and (tmp_path / "out" / runtime.EXE).exists()


# The graphics card: Windows' list, matched to llama.cpp's

def test_the_card_is_matched_to_windows_whatever_the_spelling():
    windows = {"NVIDIA GeForce RTX 4060 Laptop GPU": 8188, "Intel(R) UHD Graphics": 128,
               "AMD Radeon RX 7600": 8176}
    assert runtime._matching("NVIDIA GeForce RTX 4060 Laptop GPU", windows) == 8188
    assert runtime._matching("Intel(R) UHD Graphics", windows) == 128
    assert runtime._matching("AMD Radeon RX 7600 (RADV NAVI33)", windows) == 8176
    assert runtime._matching("Some Other GPU", windows) is None


def test_integrated_graphics_never_wins_even_when_it_reports_more_memory():
    found = [runtime.Device("Vulkan0", "AMD Radeon(TM) Graphics", 16384, None),
             runtime.Device("Vulkan1", "NVIDIA GeForce RTX 3060", 12288, None),
             runtime.Device("Vulkan2", "Intel(R) Iris(R) Xe Graphics", 8000, None)]
    assert runtime.best_gpu(found).id == "Vulkan1"
    assert runtime.best_gpu(found[::2]) is None   # integrated only: the processor
    known = [runtime.Device("Vulkan0", "AMD Radeon(TM) Graphics", 16384, 512),
             runtime.Device("Vulkan1", "NVIDIA GeForce GTX 1650", 4096, 4096)]
    assert runtime.best_gpu(known).name == "NVIDIA GeForce GTX 1650"


def test_a_pc_without_a_graphics_card_gets_the_processor_build_only(monkeypatch):
    installed = []
    monkeypatch.setattr(runtime, "planned_gpu", lambda: None)
    monkeypatch.setattr(runtime, "install", lambda backend, progress=None: installed.append(backend))
    assert runtime.choose("auto") == runtime.Choice("cpu", None)
    assert installed == ["cpu"]   # no Vulkan build fetched for nothing


def test_the_card_chosen_is_remembered_by_name(monkeypatch, tmp_path):
    monkeypatch.setenv("DOWSE_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "planned_gpu", lambda: "NVIDIA GeForce RTX 3060")
    monkeypatch.setattr(runtime, "install", lambda backend, progress=None: None)
    monkeypatch.setattr(runtime, "devices", lambda backend: [
        runtime.Device("Vulkan0", "AMD Radeon(TM) Graphics", 16384, 512),
        runtime.Device("Vulkan1", "NVIDIA GeForce RTX 3060", 12288, 12288)])
    choice = runtime.choose("auto")
    assert choice == runtime.Choice("vulkan", "Vulkan1", "NVIDIA GeForce RTX 3060")
    runtime.save(choice)
    assert runtime.load() == choice
    (tmp_path / "runtime.json").write_text(json.dumps({"backend": "vulkan", "device": "Vulkan0"}))   # an older file
    assert runtime.load() == runtime.Choice("vulkan", "Vulkan0", None)


def test_listing_devices_never_breaks_setup(monkeypatch):
    def hangs(*_, **__):
        raise runtime.subprocess.TimeoutExpired("llama-server", 60)

    monkeypatch.setattr(runtime, "installed", lambda backend: True)
    monkeypatch.setattr(runtime.subprocess, "run", hangs)
    assert runtime.devices("vulkan") == []


def test_a_card_that_fails_to_start_leaves_search_on_the_processor(monkeypatch):
    tried = []

    def start(self, timeout, *, cancel=None):
        tried.append(self.choice)
        if self.choice.device:
            raise OSError("llama-server quit (-1073741819)")
        return self

    monkeypatch.setattr(runtime.Server, "_start", start)
    server = runtime.Server(runtime.Choice("vulkan", "Vulkan0", "Old GPU")).start()
    assert server.fell_back and server.choice == runtime.Choice("vulkan", None)
    assert [c.device for c in tried] == ["Vulkan0", None]
    strict = runtime.Server(runtime.Choice("vulkan", "Vulkan0"), fallback=False)
    with pytest.raises(OSError):
        strict.start()


def test_first_setup_falls_back_to_the_processor_when_the_card_cannot_run_the_model(monkeypatch):
    from dowse import modelfiles, speed

    gpu, cpu = runtime.Choice("vulkan", "Vulkan0", "Old GPU"), runtime.Choice("cpu", None)
    monkeypatch.setattr(runtime, "choose", lambda backend, progress=None: gpu if backend == "auto" else cpu)
    monkeypatch.setattr(runtime, "load", lambda: None)
    monkeypatch.setattr(modelfiles, "missing", lambda *a, **k: [])
    monkeypatch.setattr(modelfiles, "damaged", lambda *a, **k: [])
    monkeypatch.setattr(speed, "load", lambda *a, **k: speed.GUESS[False])
    monkeypatch.setattr(speed, "measure", lambda *a, **k: None)
    validated = []

    def validate(self, choice):
        validated.append(choice)
        if choice.device:
            raise RuntimeError("The model could not produce valid embeddings")

    monkeypatch.setattr(setup.SetupJob, "_validate", validate)
    finished = []
    job = setup.SetupJob("auto", lambda p: None, finished.append, finished.append, fallback=True)
    job._run()
    assert finished == [cpu] and validated == [gpu, cpu]
    switching = setup.SetupJob("auto", lambda p: None, finished.append, finished.append, fallback=False)
    switching._run()   # asked for the card in Settings: told why, not silently put on the processor
    assert "valid embeddings" in finished[-1]


# The Visual C++ runtime, the installer's quit, the installed program

def test_the_visual_cpp_runtime_goes_beside_each_build(monkeypatch, tmp_path):
    redist = tmp_path / "redist"
    redist.mkdir()
    for name in runtime.REDIST:
        (redist / name).write_bytes(b"dll")
    build = tmp_path / "build"
    build.mkdir()
    (build / "msvcp140.dll").write_bytes(b"its own")   # one the build already has stays
    monkeypatch.setattr(runtime, "redist_folder", lambda: redist)
    runtime._add_redist(build)
    assert (build / "vcruntime140.dll").read_bytes() == b"dll"
    assert (build / "msvcp140.dll").read_bytes() == b"its own"


def test_quit_with_no_app_running_returns_at_once(monkeypatch, tmp_path):
    monkeypatch.setenv("DOWSE_HOME", str(tmp_path))
    assert launch.message(["--quit"])[0] == {"op": "quit"}
    assert launch.quit_app(timeout=2) == 0


def test_quit_tells_the_installer_the_app_was_running_even_when_its_reply_is_lost(monkeypatch, tmp_path):
    """An app asked to quit may close before its reply arrives (found updating in Windows Sandbox): the startup lock
    it held, not the reply, says it was running, so an update starts it again."""
    import subprocess

    monkeypatch.setenv("DOWSE_HOME", str(tmp_path))
    go = tmp_path / "go"
    app = subprocess.Popen([sys.executable, "-c", (
        "import pathlib, sys, time; from dowse.desktop.launch import StartupLock; lock = StartupLock();"
        "assert lock.acquire(); print('held', flush=True); go = pathlib.Path(sys.argv[1])\n"
        "while not go.exists(): time.sleep(0.05)"), str(go)],
        stdout=subprocess.PIPE, text=True, env={**os.environ, "DOWSE_HOME": str(tmp_path)})
    try:
        assert app.stdout.readline().strip() == "held"

        def quits_without_answering(request):
            go.write_text("")   # the app goes…
            return False        # …and its answer never comes
        monkeypatch.setattr(launch, "send", quits_without_answering)
        assert launch.quit_app(timeout=10) == launch.QUIT_WAS_RUNNING
    finally:
        go.write_text("")
        app.wait(10)


def test_the_installed_app_starts_itself_with_dowse_exe(monkeypatch, tmp_path):
    (tmp_path / "python.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "Lib" / "dowse" / "desktop" / "launch.py")])
    (tmp_path / "pythonw.exe").write_bytes(b"")
    assert winapi.launcher() == (str(tmp_path / "pythonw.exe"), ["-m", "dowse.desktop.launch"])
    # A venv's dowse.exe is the command line (Windows' names ignore case): not the app's Dowse.exe
    (tmp_path / "Dowse.exe").write_bytes(b"")
    assert winapi.launcher() == (str(tmp_path / "pythonw.exe"), ["-m", "dowse.desktop.launch"])
    (tmp_path / "python312._pth").write_text("python312.zip\n.\n")   # the embeddable Python the installer ships
    assert winapi.launcher() == (str(tmp_path / "Dowse.exe"), ["-m", "dowse.desktop.launch"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' resource functions")
def test_dowse_exe_has_dowse_s_name_and_icon(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import exe_resources

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pytest.skip("no pythonw.exe beside this Python")
    exe = Path(shutil.copy2(pythonw, tmp_path / "Dowse.exe"))
    exe_resources.set_icon(exe, Path(__file__).resolve().parents[1] / "assets" / "brand" / "dowse.ico")
    exe_resources.set_version(exe, "Dowse", "1.2.3", "Dowse")
    assert "Dowse".encode("utf-16-le") in exe.read_bytes()
    assert exe_resources._existing(exe, exe_resources.RT_GROUP_ICON)
    import ctypes
    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(exe), None)
    data = ctypes.create_string_buffer(size)
    assert ctypes.windll.version.GetFileVersionInfoW(str(exe), 0, size, data)
    value = ctypes.c_wchar_p()
    length = ctypes.c_uint()
    assert ctypes.windll.version.VerQueryValueW(data, r"\StringFileInfo\040904B0\FileDescription",
                                                ctypes.byref(value), ctypes.byref(length))
    assert value.value == "Dowse"
