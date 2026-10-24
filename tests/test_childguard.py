"""Outside Windows, llama-server runs under a guard that stops it when Dowse ends, however Dowse ends (Windows has its
job object, `runtime._die_with_us`)."""

import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Windows ties processes with a job object")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until(check, seconds=8.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def program(tmp_path):
    """A stand-in for llama-server: writes its pid, then waits to be stopped."""
    return [sys.executable, "-c",
            f"import os, time; open({str(tmp_path / 'pid')!r}, 'w').write(str(os.getpid())); time.sleep(120)"]


def test_the_program_stops_when_dowse_is_killed(tmp_path):
    # A "Dowse" that starts the guard as runtime.Server does, then is killed outright (no chance to clean up).
    dowse = textwrap.dedent(f"""
        import subprocess, sys, time
        subprocess.Popen([sys.executable, "-m", "dowse.childguard", *{program(tmp_path)!r}], stdin=subprocess.PIPE)
        time.sleep(120)
    """)
    parent = subprocess.Popen([sys.executable, "-c", dowse])
    try:
        assert wait_until(lambda: (tmp_path / "pid").exists() and (tmp_path / "pid").read_text())
        child = int((tmp_path / "pid").read_text())
        assert alive(child)
        parent.send_signal(signal.SIGKILL)
        parent.wait()
        assert wait_until(lambda: not alive(child)), "the program outlived a killed Dowse"
    finally:
        if parent.poll() is None:
            parent.kill()


def test_stopping_the_guard_stops_the_program_and_passes_its_exit_code(tmp_path):
    guard = subprocess.Popen([sys.executable, "-m", "dowse.childguard", *program(tmp_path)], stdin=subprocess.PIPE)
    assert wait_until(lambda: (tmp_path / "pid").exists() and (tmp_path / "pid").read_text())
    child = int((tmp_path / "pid").read_text())
    guard.terminate()
    guard.wait(15)
    assert wait_until(lambda: not alive(child))
    # the pipe kept open, as Dowse keeps it: the program ends by itself, and its code comes back
    quick = subprocess.Popen([sys.executable, "-m", "dowse.childguard", sys.executable, "-c", "raise SystemExit(7)"],
                             stdin=subprocess.PIPE)
    assert quick.wait(30) == 7
    quick.stdin.close()
