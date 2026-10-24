"""A guard that ties a program's life to Dowse's, where Windows' job object isn't there (macOS, Linux).

    python -m dowse.childguard <program> [args…]

It starts the program and holds the read end of a pipe whose write end Dowse keeps (the guard's stdin). However Dowse
ends (quit, a crash, Force Quit), its end of the pipe closes, the guard reads end-of-file, and it stops the program,
which would otherwise keep running and holding the GPU's memory. Asked to stop itself (Dowse's `Server.stop`), it
stops the program first. It exits with the program's own exit code.
"""

import os
import select
import signal
import subprocess
import sys

GRACE = 10.0   # seconds the program has to stop when asked, before it's killed


def _stop(child: subprocess.Popen) -> None:
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(GRACE)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m dowse.childguard <program> [args…]", file=sys.stderr)
        return 2
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL)

    def asked_to_stop(signum, _frame):
        _stop(child)
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, asked_to_stop)
    signal.signal(signal.SIGINT, asked_to_stop)
    watched = sys.stdin.fileno()
    while child.poll() is None:
        ready, _, _ = select.select([watched], [], [], 0.5)
        if ready and not os.read(watched, 4096):   # end of file: Dowse is gone
            _stop(child)
            break
    code = child.wait()
    return code if code >= 0 else 128 - code   # stopped by a signal: the shell's convention (SIGTERM: 143)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
