"""llama.cpp as a sidecar: the official llama-server build for this PC's hardware, started and stopped on demand.

No compiling: ggml-org publishes Windows and macOS builds for every backend, and the stock `/v1/embeddings` endpoint
takes text, images and audio for EmbeddingGemma 2. Backends agree (CPU against Vulkan: cosine ≥ 0.9994 on every reference
input, tests/test_reference.py) once images arrive at their final size (`embedder.sized`).

Backends:
  vulkan  any GPU (NVIDIA, AMD, Intel), a 30 MB download. The default when there's a GPU worth using.
  cuda    NVIDIA only, ~550 MB with its runtime DLLs. Opt-in.
  cpu     everywhere. Queries take the same ~50 ms; indexing images is ~7× slower (see `profile`).
  metal   macOS on Apple Silicon: the GPU through Metal, in the same 12 MB build as the processor's.

Any PC must end up with something that works: no graphics card, one without a Vulkan driver, a driver that crashes
on the model, a PC without the Visual C++ runtime. The card is the one Windows lists with the most dedicated memory
(`graphics_cards`, read on this PC, never assumed); a card that can't run the model leaves Dowse on the processor
(`Server.start` falls back, and setup validates the processor instead); the C++ runtime the builds need travels with
the installed app and is copied beside each build (`REDIST`).
"""

import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from . import modelfiles, paths
from .download import Progress, fetch

TAG = "b11574"
RELEASES = f"https://github.com/ggml-org/llama.cpp/releases/download/{TAG}"


def _platform_assets() -> dict[str, list[str]]:
    """The archives each backend needs on this system, unpacked into one folder."""
    if sys.platform == "darwin":
        # One build holds both: Metal on Apple Silicon, and the processor (`--device none`). An Intel Mac: the processor.
        build = f"llama-{TAG}-bin-macos-{'arm64' if platform.machine() == 'arm64' else 'x64'}.tar.gz"
        return {"cpu": [build], "metal": [build]} if platform.machine() == "arm64" else {"cpu": [build]}
    if sys.platform.startswith("linux"):
        # NVIDIA cards run on Vulkan too (llama.cpp's CUDA build for Linux needs a 600 MB runtime beside it)
        arch = "arm64" if platform.machine() in ("aarch64", "arm64") else "x64"
        return {"cpu": [f"llama-{TAG}-bin-ubuntu-{arch}.tar.gz"],
                "vulkan": [f"llama-{TAG}-bin-ubuntu-vulkan-{arch}.tar.gz"]}
    return {
        "cpu": [f"llama-{TAG}-bin-win-cpu-x64.zip"],
        "vulkan": [f"llama-{TAG}-bin-win-vulkan-x64.zip"],
        "cuda": [f"llama-{TAG}-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip"],
    }


ASSETS = _platform_assets()
BACKENDS = tuple(ASSETS)
GPU_BACKEND = "metal" if sys.platform == "darwin" else "vulkan"   # what `choose("auto")` runs a GPU with
EXE = "llama-server.exe" if sys.platform == "win32" else "llama-server"

# A GPU with less dedicated memory than this is integrated (its "memory" is borrowed system RAM): auto mode uses the
# CPU instead. llama.cpp can't tell: it lists an AMD APU's 16 GB of shared memory above a 12 GB RTX 4070 Ti.
MIN_DEDICATED_MB = 2048
# The Visual C++ runtime llama.cpp's Windows builds link against (MSVCP140, VCRUNTIME140, VCRUNTIME140_1). Windows
# doesn't always have it; the installer ships these files (`redist` beside the app's Python), copied into each build.
REDIST = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")

log = logging.getLogger("dowse.runtime")


@dataclass(frozen=True)
class Device:
    id: str          # what llama-server's --device takes ("Vulkan1", "CUDA0")
    name: str
    memory_mb: int   # as llama.cpp reports it (shared memory included for integrated GPUs)
    dedicated_mb: int | None = None   # from Windows, when it knows the adapter


@dataclass(frozen=True)
class Choice:
    """What to run on: written to settings by `setup`, read by `Server`."""
    backend: str
    device: str | None   # None: the CPU
    name: str | None = None   # the graphics card's name, as Windows and llama.cpp give it (None: the CPU)

    def describe(self) -> str:
        return f"{self.backend}" + (f" on {self.device}" if self.device else "")


def folder(backend: str) -> Path:
    return paths.runtimes() / f"{TAG}-{backend}"


def executable(backend: str) -> Path:
    return folder(backend) / EXE


def installed(backend: str) -> bool:
    return executable(backend).exists()


def redist_folder() -> Path:
    """Where the installed app keeps the Visual C++ runtime files: beside its Python."""
    return Path(sys.executable).parent / "redist"


def _unpack(url: str, archive: Path, into: Path, progress: Progress | None) -> None:
    """Downloads and unpacks one archive (a Windows zip, a macOS .tar.gz). One that's damaged (cut short, a bad
    sector) is fetched again once."""
    for attempt in range(2):
        fetch(url, archive, progress=progress)
        try:
            if archive.name.endswith(".tar.gz"):
                _untar(archive, into)
                return
            with zipfile.ZipFile(archive) as zip_file:
                if zip_file.testzip() is None:
                    zip_file.extractall(into)
                    return
        except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError):
            pass
        archive.unlink(missing_ok=True)
    raise OSError(f"{archive.name} arrived damaged twice: check the connection and try again")


def _untar(archive: Path, into: Path) -> None:
    """A .tar.gz unpacked into `into`, its files lifted out of the one folder the archive keeps them in
    (llama-b11574/). The `data` filter refuses paths and links that would land outside `into`."""
    unpacked = into.with_name(into.name + ".untar")
    shutil.rmtree(unpacked, ignore_errors=True)
    unpacked.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(unpacked, filter="data")
    entries = list(unpacked.iterdir())
    top = entries[0] if len(entries) == 1 and entries[0].is_dir() else unpacked
    into.mkdir(parents=True, exist_ok=True)
    for entry in top.iterdir():
        target = into / entry.name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        entry.rename(target)
    shutil.rmtree(unpacked, ignore_errors=True)


def install(backend: str, progress: Progress | None = None) -> Path:
    """Downloads and unpacks `backend`'s build (once). It's put together in a staging folder and moved into place
    whole, so an interrupted install leaves nothing that looks installed; downloads resume where they stopped."""
    if installed(backend):
        _add_redist(folder(backend))
        return folder(backend)
    target = folder(backend)
    downloads = paths.runtimes() / "downloads"
    staging = target.with_name(target.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    for asset in ASSETS[backend]:
        _unpack(f"{RELEASES}/{asset}", downloads / asset, staging, progress)
    if not (staging / EXE).exists():
        raise OSError(f"{EXE} isn't in {', '.join(ASSETS[backend])}")
    _add_redist(staging)
    _runs(staging / EXE)
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    for asset in ASSETS[backend]:
        (downloads / asset).unlink(missing_ok=True)
    return target


def _add_redist(into: Path) -> None:
    """The Visual C++ runtime beside the build, from the installed app, where it isn't there already."""
    for name in REDIST:
        source = redist_folder() / name
        if source.exists() and not (into / name).exists():
            shutil.copy2(source, into / name)


def _runs(exe: Path) -> None:
    """The build starts at all (it prints its version): a missing system library is said plainly, here, rather
    than as a server that won't start later."""
    try:
        out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60, **_no_window())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OSError(f"llama.cpp couldn't start: {error}") from error
    if out.returncode in (0xC0000135, -1073741515):   # STATUS_DLL_NOT_FOUND
        raise OSError("llama.cpp needs the Microsoft Visual C++ runtime, which this PC lacks: reinstall Dowse, or "
                      "install the Visual C++ Redistributable (x64) from Microsoft")


def _no_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


_job = None


def _die_with_us(process: subprocess.Popen) -> None:
    """Ties a child process's life to this one's: it's put in a Windows job object that kills its processes when
    the job's last handle closes, which happens when this process ends, however it ends (Ctrl+C, a crash, Task
    Manager). Without it, a killed Dowse leaves llama-server holding the GPU's memory. (Elsewhere: `childguard`.)"""
    global _job
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    if _job is None:
        class Limits(ctypes.Structure):   # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD), ("IoCounters", ctypes.c_uint64 * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = kernel32.CreateJobObjectW(None, None)
        limits = Limits(LimitFlags=0x2000)   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            return   # no job: the server still stops with stop(), just not on a crash
        _job = job
    handle = kernel32.OpenProcess(0x0100 | 0x0001, False, process.pid)   # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    if handle:
        kernel32.AssignProcessToJobObject(_job, handle)
        kernel32.CloseHandle(handle)


_DEVICE_LINE = re.compile(r"^\s*(\w+\d+):\s*(.+?)\s*\((\d+) MiB,")


def devices(backend: str) -> list[Device]:
    """The GPUs `backend`'s build sees (none for cpu; none either when the build can't list them: no Vulkan driver,
    a driver that hangs)."""
    if backend == "cpu" or not installed(backend):
        return []
    try:
        out = subprocess.run([executable(backend), "--list-devices"], capture_output=True, text=True, timeout=60,
                             **_no_window())
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("%s couldn't list its devices: %s", backend, error)
        return []
    dedicated = _dedicated_memory()
    found = []
    for line in (out.stdout + out.stderr).splitlines():
        if match := _DEVICE_LINE.match(line):
            name = match.group(2)
            found.append(Device(match.group(1), name, int(match.group(3)), _matching(name, dedicated)))
    return found


def _simple(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"\((r|tm|c)\)", " ", name.lower())).strip()


def _matching(name: str, dedicated: dict[str, int]) -> int | None:
    """The dedicated memory Windows records for the adapter llama.cpp calls `name`: the same name, or the same once
    "(R)", "(TM)" and punctuation are set aside, or one name inside the other ("AMD Radeon RX 7600" against "AMD
    Radeon RX 7600 (RADV NAVI33)")."""
    if name in dedicated:
        return dedicated[name]
    wanted = _simple(name)
    for known, size in dedicated.items():
        simple = _simple(known)
        if simple and (simple == wanted or simple in wanted or wanted in simple):
            return size
    return None


def _dedicated_memory() -> dict[str, int]:
    """Each display adapter's dedicated memory in MiB, as Windows records it (Linux: its drivers), by name."""
    if sys.platform.startswith("linux"):
        return _linux_cards()
    if sys.platform != "win32":
        return {}
    import winreg

    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    found: dict[str, int] = {}
    try:
        adapters = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return found
    index = 0
    while True:
        try:
            sub = winreg.EnumKey(adapters, index)
        except OSError:
            break
        index += 1
        try:
            key = winreg.OpenKey(adapters, sub)
            name = winreg.QueryValueEx(key, "DriverDesc")[0]
            raw = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]
        except OSError:
            continue
        size = int.from_bytes(raw, "little") if isinstance(raw, bytes) else int(raw)
        found[name] = max(found.get(name, 0), size >> 20)
    return found


def _linux_cards(drm: Path = Path("/sys/class/drm")) -> dict[str, int]:
    """Linux's graphics cards with memory of their own: NVIDIA's from nvidia-smi (named as Vulkan names them), AMD's
    from the amdgpu driver (its VRAM; an APU's is a small carve-out, under `MIN_DEDICATED_MB`)."""
    found: dict[str, int] = {}
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines():
            name, _, memory = line.rpartition(",")
            if name.strip() and memory.strip().isdigit():
                found[name.strip()] = int(memory)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for card in sorted(drm.glob("card[0-9]*")):
        vram = card / "device" / "mem_info_vram_total"
        if "-" in card.name or not vram.exists():   # card0-DP-1: a connector, not a card
            continue
        try:
            size = int(vram.read_text().strip()) >> 20
        except (OSError, ValueError):
            continue
        name = f"AMD graphics ({card.name})"
        try:
            name = (card / "device" / "product_name").read_text().strip() or name
        except OSError:
            pass
        found[name] = max(found.get(name, 0), size)
    return found


def graphics_cards() -> dict[str, int]:
    """The PC's display adapters by name, with their dedicated memory (MiB): known before anything is installed."""
    return _dedicated_memory()


def planned_gpu() -> str | None:
    """The graphics card `choose("auto")` will use, by name, or None (the CPU)."""
    if sys.platform == "darwin":   # Apple Silicon's GPU is always there; an Intel Mac indexes on the processor
        return "Apple GPU" if "metal" in ASSETS else None
    cards = {name: mb for name, mb in graphics_cards().items() if mb >= MIN_DEDICATED_MB}
    return max(cards, key=cards.get) if cards else None


# Integrated graphics by name, for a card Windows has no memory size for (then llama.cpp's figure includes the shared
# system memory, and an integrated GPU looks big).
_INTEGRATED = re.compile(r"\b(uhd|iris|hd graphics|radeon\s+graphics|vega\s+\d+|\d{3}m\s+graphics|basic render|llvmpipe)\b")


def integrated(name: str) -> bool:
    return bool(_INTEGRATED.search(re.sub(r"\((r|tm)\)", " ", name.lower())))


def best_gpu(found: list[Device]) -> Device | None:
    """The GPU worth indexing on: the one with the most dedicated memory, if that's enough to be a discrete GPU."""
    def dedicated(device: Device) -> int:
        if device.dedicated_mb is not None:
            return device.dedicated_mb
        return 0 if integrated(device.name) else device.memory_mb

    usable = [device for device in found if dedicated(device) >= MIN_DEDICATED_MB]
    return max(usable, key=dedicated, default=None)


def choose(backend: str = "auto", device: str | None = None, progress: Progress | None = None) -> Choice:
    """Picks (and installs) the backend. `auto`: the GPU backend (Vulkan; Metal on a Mac) on a GPU worth
    it, else the CPU (without fetching a GPU build at all when the system lists no card worth it)."""
    if backend == "cpu":
        install("cpu", progress)
        return Choice("cpu", None)
    if backend in ("vulkan", "cuda", "metal"):
        install(backend, progress)
        found = devices(backend)
        gpu = next((d for d in found if d.id == device), None) if device else best_gpu(found) or next(iter(found), None)
        if gpu is None:
            raise OSError(f"{backend} sees no GPU on this PC")
        return Choice(backend, gpu.id, gpu.name)
    if backend != "auto":
        raise ValueError(f"backend must be auto or one of {', '.join(BACKENDS)}")
    if planned_gpu() is not None:
        install(GPU_BACKEND, progress)
        if gpu := best_gpu(devices(GPU_BACKEND)):
            return Choice(GPU_BACKEND, gpu.id, gpu.name)
    install("cpu", progress)
    return Choice("cpu", None)


# Settings

def settings_file() -> Path:
    return paths.home() / "runtime.json"


def save(choice: Choice) -> None:
    settings_file().parent.mkdir(parents=True, exist_ok=True)
    settings_file().write_text(json.dumps(asdict(choice) | {"tag": TAG}, indent=2))


def load() -> Choice | None:
    try:
        data = json.loads(settings_file().read_text())
        return Choice(data["backend"], data.get("device"), data.get("name"))
    except (OSError, ValueError, KeyError, TypeError):
        return None


# The server

def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Server:
    """One llama-server process with EmbeddingGemma 2: `text_only` loads only the text model (queries, ~310 MB),
    otherwise the image and audio encoders too (indexing). Use as a context manager."""

    # One input must fit one micro-batch (embeddings are non-causal): 2048 tokens is the reference's cut, and it also
    # holds a 1120-token image or a 30 s audio window (~750 tokens). Queries are short: 512.
    MAX_TOKENS = 2048
    QUERY_TOKENS = 512

    def __init__(self, choice: Choice, *, text_only: bool = False, slots: int | None = None,
                 models: Path | None = None, log: Path | None = None, model: str | modelfiles.Model | None = None,
                 fallback: bool = True):
        self.choice = choice
        self.fallback = fallback   # a GPU that fails to start: run on the processor instead (not when validating)
        self.fell_back = False
        self.model = modelfiles.get(model)
        self.text_only = text_only
        # Parallel requests: the vision encoder takes one image at a time, so a GPU is kept busy by several requests
        # in flight. On the CPU they'd only split its threads.
        self.slots = slots or (1 if text_only else (4 if choice.device else 2))
        self.models = models
        self.log_path = log or paths.logs() / ("server-query.log" if text_only else "server.log")
        self.process: subprocess.Popen | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def command(self) -> list[str]:
        tokens = self.QUERY_TOKENS if self.text_only else self.MAX_TOKENS
        command = [str(executable(self.choice.backend)),
                   "-m", str(modelfiles.path(self.model.text, self.models)),
                   "--embedding", "--pooling", "mean",
                   "-c", str(tokens * self.slots), "-b", str(tokens), "-ub", str(tokens), "-np", str(self.slots),
                   "--host", "127.0.0.1", "--port", str(self.port), "--no-webui"]
        if self.text_only or self.model.media is None:
            command += ["--no-mmproj"]
        else:
            # Images arrive already sized for their token budget (`imagesizing`): mtmd must take them as they are.
            command += ["--mmproj", str(modelfiles.path(self.model.media, self.models)),
                        "--image-min-tokens", "1", "--image-max-tokens", "1120"]
        if self.choice.device:
            command += ["--device", self.choice.device, "-ngl", "999"]
        else:
            command += ["--device", "none", "-ngl", "0"]
        return command

    def start(self, timeout: float = 180, *, cancel=None) -> "Server":
        try:
            return self._start(timeout, cancel=cancel)
        except (OSError, TimeoutError) as error:
            if not (self.fallback and self.choice.device) or (cancel and cancel()):
                raise
            # A driver update, a card taken out, another program holding its memory: the processor still works
            # (every build has its CPU backends), so search goes on, slower, rather than not at all.
            log.warning("%s failed (%s): running on the processor", self.choice.describe(), str(error).splitlines()[0])
            self.choice = Choice(self.choice.backend, None)
            self.slots = 1 if self.text_only else 2
            self.fell_back = True
            return self._start(timeout, cancel=cancel)

    def _start(self, timeout: float, *, cancel=None) -> "Server":
        if cancel and cancel():
            raise InterruptedError("Model preparation cancelled")
        missing = modelfiles.missing(self.models, model=self.model)
        if self.model.text in missing or (not self.text_only and self.model.media in missing):
            raise OSError("the model isn't downloaded yet: run `dowse setup`")
        if not installed(self.choice.backend):
            raise OSError(f"the {self.choice.backend} runtime isn't installed: run `dowse setup`")
        self.port = _free_port()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("wb")
        if sys.platform == "win32":
            self.process = subprocess.Popen(self.command(), stdout=log, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, **_no_window())
            _die_with_us(self.process)
        else:
            # No job object outside Windows: a guard holds a pipe this process keeps open, and stops the server when
            # it closes, however this process ends (`childguard`).
            self.process = subprocess.Popen([sys.executable, "-m", "dowse.childguard", *self.command()],
                                            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.PIPE)
        log.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel and cancel():
                self.stop()
                raise InterruptedError("Model preparation cancelled")
            if self.process.poll() is not None:
                raise OSError(f"llama-server quit ({self.process.returncode}); see {self.log_path}\n"
                              + self._log_tail())
            try:
                if httpx.get(self.url + "/health", timeout=2).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        self.stop()
        raise TimeoutError(f"llama-server didn't get ready in {timeout:.0f} s; see {self.log_path}")

    def _log_tail(self, lines: int = 15) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(15)   # (outside Windows the guard gives the server 10 s, then kills it)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.process and self.process.stdin:
            self.process.stdin.close()
        self.process = None

    def __enter__(self) -> "Server":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()


def environment_summary() -> str:
    """For `dowse status`: which runtimes are here, and what each sees."""
    lines = []
    for backend in BACKENDS:
        if installed(backend):
            found = devices(backend)
            listed = ", ".join(f"{d.id} {d.name} ({_memory(d)})" for d in found)
            lines.append(f"  {backend}: installed" + (f" — {listed}" if listed else ""))
        else:
            lines.append(f"  {backend}: not installed")
    return "\n".join(lines)


def _memory(device: Device) -> str:
    """A GPU's memory as the system tells it: dedicated (Windows), else what llama.cpp reports (a Mac's GPU shares
    the computer's memory)."""
    if device.dedicated_mb is not None:
        return f"{device.dedicated_mb} MB dedicated"
    return f"{device.memory_mb} MB"
