"""Starts the app (`launch` decides it's this process's turn): data moved from old places, the index encrypted or
not, logging, then the engine, the local server and the windows. Runs until Quit."""

import logging
import logging.handlers
import sys
import threading

from .. import migrate, paths, privacy, settings
from . import winapi
from .brand import APP_ID

log = logging.getLogger("dowse.app")


def _logging() -> None:
    """A small rotating log in Logs\\app.log. The app runs without a console (pythonw), so anything that goes wrong
    is written here instead."""
    paths.logs().mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(paths.logs() / "app.log", maxBytes=1 << 20, backupCount=2,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    for noisy in ("httpx", "pywebview", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.excepthook = lambda kind, error, trace: log.error("uncaught", exc_info=(kind, error, trace))
    threading.excepthook = lambda args: log.error("uncaught in %s", args.thread.name if args.thread else "?",
                                                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def run(background: bool = False, pending: list[dict] = (), lock=None) -> int:
    migrate.run()   # before anything opens a file in the data folder
    privacy.apply(bool(settings.load().get("encrypt_index", False)))   # (it can't once they're open)
    _logging()
    winapi.set_app_id(APP_ID)
    from .app import App
    from .native import Native

    app = App(native_factory=Native)
    app.start()
    show = None
    if app.choice is None or not (app.selection.folders or app.selection.files):
        show = "/welcome"
    elif not background:
        show = "popup"
    for request in pending:
        app.request(request)
    log.info("started (%s) on %s", app.choice.describe() if app.choice else "not set up", app.server.url)
    app.native.run(show)
    app.stop()
    del lock
    return 0
