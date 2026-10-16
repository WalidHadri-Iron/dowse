"""Tests the built installer on a fresh Windows: Windows Sandbox (Windows 10/11 Pro, "Windows Sandbox" turned on),
once without a graphics card and once with the sandbox's virtual one. Each run installs Dowse silently, sets it up,
indexes and searches a small library, starts the app, updates it while it runs, interrupts an install and repairs
it, then uninstalls (packaging/sandbox/test.ps1). Nothing on this PC is touched: the sandbox is thrown away.

    uv run python scripts/sandbox_test.py [--runs nogpu,vgpu] [--keep]      (on Windows; build the installer first)

Results (results.json per run, the installer's logs, screenshots) are copied to build/sandbox/<run>.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsSandbox.exe"
RUNS = {"nogpu": "Disable", "vgpu": "Enable"}


def config(folder: Path, run: str) -> str:
    return f"""<Configuration>
  <vGPU>{RUNS[run]}</vGPU>
  <Networking>Default</Networking>
  <MemoryInMB>8192</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>{folder}</HostFolder><SandboxFolder>C:\\Test</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell.exe -ExecutionPolicy Bypass -WindowStyle Minimized -File C:\\Test\\test.ps1 -Run {run}</Command>
  </LogonCommand>
</Configuration>
"""


def sandbox_running() -> bool:
    listed = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WindowsSandboxClient.exe"], capture_output=True, text=True)
    return "WindowsSandboxClient.exe" in listed.stdout


def close(sandbox: subprocess.Popen) -> None:
    """Closes the sandbox this run opened, and waits until it's gone: only one can run at a time."""
    subprocess.run(["taskkill", "/F", "/IM", "WindowsSandboxClient.exe"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/PID", str(sandbox.pid)], capture_output=True)
    deadline = time.monotonic() + 120
    while sandbox_running() and time.monotonic() < deadline:
        time.sleep(2)
    time.sleep(5)   # its container is torn down a moment after the window goes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", default="nogpu,vgpu")
    parser.add_argument("--keep", action="store_true", help="leave each sandbox open at the end (close it to go on)")
    parser.add_argument("--timeout", type=int, default=3600, help="seconds a run may take")
    args = parser.parse_args()
    if not SANDBOX.exists():
        sys.exit("Windows Sandbox isn't turned on (Windows features › Windows Sandbox, then restart)")
    if sandbox_running():
        sys.exit("A Windows Sandbox is already open (only one can run): close it first")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    setup = ROOT / "dist" / f"Dowse-Setup-{version}.exe"
    if not setup.exists():
        sys.exit(f"{setup} isn't built: uv run python scripts/build_installer.py")
    folder = Path(os.environ["TEMP"]) / "dowse-sandbox"   # on C:, where the sandbox can map it
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "results").mkdir(parents=True)
    shutil.copy2(setup, folder / "Dowse-Setup.exe")
    shutil.copy2(ROOT / "packaging" / "sandbox" / "test.ps1", folder / "test.ps1")
    out = ROOT / "build" / "sandbox"
    failed = False
    for run in args.runs.split(","):
        wsb = folder / f"{run}.wsb"
        wsb.write_text(config(folder, run), encoding="utf-8")
        print(f"{run}: starting a sandbox ({'no GPU' if RUNS[run] == 'Disable' else 'virtual GPU'})…", flush=True)
        sandbox = subprocess.Popen([str(SANDBOX), str(wsb)])
        done = folder / "results" / run / "done"
        started = time.monotonic()
        while not done.exists() and time.monotonic() - started < args.timeout:
            time.sleep(10)
        if args.keep:
            print(f"{run}: done; close the sandbox to go on", flush=True)
            while sandbox_running():
                time.sleep(5)
        else:
            close(sandbox)
        result = folder / "results" / run / "results.json"
        shutil.rmtree(out / run, ignore_errors=True)
        if (folder / "results" / run).exists():
            shutil.copytree(folder / "results" / run, out / run)
        if not result.exists():
            print(f"{run}: no results after {args.timeout} s", flush=True)
            failed = True
            continue
        report = json.loads(result.read_text(encoding="utf-8-sig"))
        for step in report["steps"]:
            mark = "ok  " if step["ok"] else "FAIL"
            print(f"  {mark} {step['step']} ({step['seconds']} s)" + (f": {step['error']}" if step["error"] else ""))
        failed |= not report.get("ok")
    print(f"Results: {out}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
