"""How Dowse starts, and how anything outside it (Explorer's right-click menu, Send to, the command line) asks the
running app to do something. Light on purpose: no Qt here, so a request reaches the app in a fraction of a second.

    dowse-app                         open the app (or, if it's running, its search panel)
    dowse-app --background            start in the tray (as Windows does at sign-in)
    dowse-app --add PATH…             add files or folders to what Dowse searches
    dowse-app --remove PATH…          take them out
    dowse-app --search FOLDER         the search panel, searching only in FOLDER
    dowse-app --like FILE             the files most like FILE (in the index or not)
    dowse-app --library               open the Library (what Dowse searches, file by file)
    dowse-app --quit                  quit the running app, and wait until it's gone (the installer's way)

Explorer runs its menu commands once per selected file, so twelve selected files are twelve launches at once. Each
hands its request to the running app over a named pipe; the app gathers them into one change. If the app isn't
running, the first launch to take the startup lock becomes the app (with its request queued) and the others wait
for it, then hand theirs over: one app, every file.
"""

import argparse
import getpass
import json
import os
import sys
import time

from .. import paths

PIPE_TIMEOUT = 15.0   # seconds a launch waits for an app that's starting


def pipe_name() -> str:
    return f"dowse-{getpass.getuser()}"


def message(argv: list[str]) -> tuple[dict, bool]:
    """(the request, start in the background) from the command line."""
    parser = argparse.ArgumentParser(prog="dowse-app")
    parser.add_argument("--background", action="store_true", help="start in the tray, without opening the panel")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--add", nargs="+", metavar="PATH", help="add files or folders to what Dowse searches")
    group.add_argument("--remove", nargs="+", metavar="PATH", help="take files or folders out")
    group.add_argument("--search", metavar="FOLDER", help="search only in this folder")
    group.add_argument("--like", metavar="FILE", help="find the files most like this one")
    group.add_argument("--library", action="store_true", help="open the Library")
    group.add_argument("--quit", action="store_true", help="quit the running app and wait until it has")
    group.add_argument("--restart", action="store_true", help=argparse.SUPPRESS)   # the app, starting itself again
    args = parser.parse_args(argv)
    if args.add:
        return {"op": "add", "paths": [os.path.abspath(p) for p in args.add]}, True
    if args.remove:
        return {"op": "remove", "paths": [os.path.abspath(p) for p in args.remove]}, True
    if args.search:
        return {"op": "search", "paths": [os.path.abspath(args.search)]}, False
    if args.like:
        return {"op": "like", "paths": [os.path.abspath(args.like)]}, False
    if args.library:
        return {"op": "library"}, False
    if args.quit:
        return {"op": "quit"}, True
    if args.restart:
        return {"op": "restart"}, True
    return {"op": "show"}, args.background


def send(request: dict) -> bool:
    """Hands `request` to the running app (its local server, from `app.json`: port and token); False if none is
    listening."""
    import http.client

    try:
        running = json.loads((paths.home() / "app.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    deadline = time.monotonic() + 2.0
    while True:
        connection = http.client.HTTPConnection("127.0.0.1", int(running["port"]), timeout=5)
        try:
            connection.request("POST", "/api/request", body=json.dumps(request),
                               headers={"Content-Type": "application/json", "Cookie": f"dowse={running['token']}"})
            return connection.getresponse().status == 200
        except ConnectionRefusedError:
            return False                      # a file left by an app that's gone
        except OSError:                       # a burst of launches filled its queue: a moment later
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        finally:
            connection.close()


class StartupLock:
    """Held by the running app for its whole life: whoever holds it is the app (or about to be)."""

    def __init__(self):
        self.handle = None

    def acquire(self) -> bool:
        path = paths.home() / "app.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+b")  # noqa: SIM115 (held open for the app's life: that's the lock)
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True


QUIT_WAS_RUNNING = 2


def quit_app(timeout: float = 30) -> int:
    """Asks the running app to quit and waits for it to let go of the startup lock (its files then free to be
    replaced). Exit code for the installer: 0 when no app was running, `QUIT_WAS_RUNNING` when it was and has quit
    (an update starts it again), 1 if it's still there after `timeout` seconds."""
    asked = send({"op": "quit"})
    lock = StartupLock()
    deadline = time.monotonic() + timeout
    while not lock.acquire():
        if time.monotonic() > deadline:
            return 1
        time.sleep(0.2)
    lock.handle.close()
    return QUIT_WAS_RUNNING if asked else 0


def main(argv: list[str] | None = None) -> int:
    request, background = message(sys.argv[1:] if argv is None else argv)
    if request["op"] == "restart":   # the app quitting to start again (a setting that needs it): wait for it to go
        lock = StartupLock()
        deadline = time.monotonic() + 30
        while not lock.acquire():
            if time.monotonic() > deadline:
                return 1
            time.sleep(0.2)
        from . import main as app

        return app.run(background=True, pending=[{"op": "settings"}], lock=lock)
    if request["op"] == "quit":   # before an update or an uninstall replaces its files
        return quit_app()
    if send(request):
        return 0
    lock = StartupLock()
    if not lock.acquire():
        # Another launch is becoming the app right now: hand the request over once it listens.
        deadline = time.monotonic() + PIPE_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(0.1)
            if send(request):
                return 0
        return 1
    from . import main as app   # the heavy part, only for the launch that becomes the app

    pending = [] if request["op"] == "show" else [request]   # a plain launch opens the panel anyway
    return app.run(background=background or request["op"] in ("add", "remove"), pending=pending, lock=lock)


if __name__ == "__main__":
    sys.exit(main())
