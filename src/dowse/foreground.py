"""The person first: while someone is using Dowse (a search typed, a picture shown, a page opened), reading files steps
aside. The interface's requests mark when it was last used (`touch`); readers ask, between pieces of work (a file, a
video's frame, a stretch of sound), whether to wait (`wait`). They wait while it's in use and go on `QUIET` seconds
after, never more than `MOST` seconds at a time, so reading always moves forward. What's read doesn't change, only
when.

Measured on 10,700 files with long 4K videos being read (8 readers decoding at once, the app at 500 to 770% CPU): a
search took 0.4 to 2.2 s and the wall's pictures 8 to 17 s; with reading paused, 0.15 s and 0.01 s.
"""

import threading
import time

QUIET = 1.5   # seconds after the interface's last request before reading goes on
MOST = 10.0   # seconds a reader waits at most, at one time

_last = 0.0
_lock = threading.Lock()


def touch() -> None:
    """The interface was used just now."""
    global _last
    with _lock:
        _last = time.monotonic()


def busy() -> bool:
    return time.monotonic() - _last < QUIET


def wait() -> float:
    """Waits while the interface is in use (at most `MOST` seconds); returns how long it waited."""
    began = time.monotonic()
    while busy() and time.monotonic() - began < MOST:
        time.sleep(0.05)
    return time.monotonic() - began
