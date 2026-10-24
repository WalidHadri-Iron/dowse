"""The Mac app, started as a person starts it, on a CI Mac: it comes up in the menu bar, a second launch opens Quick
search and then the Library in its window (screenshots of each, with `screencapture`), and `--quit` ends it with
every process it started. Fails if the app doesn't start, logs an error, or doesn't quit.

    DOWSE_HOME=<a set-up home> uv run python scripts/mac_smoke.py <out dir>
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from dowse import paths


def launch(*arguments: str, wait: bool = True) -> subprocess.Popen:
    process = subprocess.Popen([sys.executable, "-m", "dowse.desktop.launch", *arguments])
    if wait:
        process.wait(60)
    return process


def until(check, seconds: float, what: str):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if result := check():
            return result
        time.sleep(0.5)
    raise SystemExit(f"timed out: {what}")


def answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def shoot(out: Path, name: str) -> None:
    picture = out / f"{name}.png"
    subprocess.run(["screencapture", "-x", str(picture)], check=False, timeout=30)
    print(f"{name}: {picture if picture.exists() else 'no screenshot (screen recording not allowed here)'}")


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    print("home:", paths.home())
    app = launch("--background", wait=False)                       # the app, in the menu bar
    state = until(lambda: (paths.home() / "app.json").exists() and json.loads((paths.home() / "app.json").read_text()),
                  90, "the app announcing itself (app.json)")
    url = f"http://127.0.0.1:{state['port']}/"   # (app.json: port, token, pid)
    until(lambda: answers(url), 30, "the app's server")
    print("app up:", url, "pid", state.get("pid"))
    launch()                                                        # a second launch: Quick search
    time.sleep(8)
    shoot(out, "quick-search")
    launch("--library")                                             # the Library, in the same window
    time.sleep(6)
    shoot(out, "library")
    quitting = launch("--quit", wait=False)
    code = quitting.wait(60)
    app.wait(30)
    print("quit:", code, "· app exit:", app.returncode)
    log = paths.logs() / "app.log"
    errors = [line for line in log.read_text(errors="replace").splitlines() if " ERROR " in line] if log.exists() else []
    for line in errors:
        print("logged:", line)
    leftover = subprocess.run(["pgrep", "-f", "llama-server|dowse.desktop.windows"], capture_output=True, text=True)
    if leftover.stdout.strip():
        print("still running:", leftover.stdout.strip())
    if code not in (0, 2) or errors or leftover.stdout.strip():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
