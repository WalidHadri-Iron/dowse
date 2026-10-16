"""The app without windows, for working on the interface: its engine and server, the page's address printed (open it
in a browser, or point `npm run dev` at it with DOWSE_API). Ctrl+C stops it.

    python -m dowse.desktop.dev [--port-file PATH]
"""

import logging
import sys
import threading

from .app import App


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    app = App()
    app.start(announce=False)
    print(f"API {app.server.url}", flush=True)
    print(f"PAGE {app.server.page()}", flush=True)
    if "--port-file" in sys.argv:
        path = sys.argv[sys.argv.index("--port-file") + 1]
        with open(path, "w", encoding="utf-8") as file:
            file.write(f"{app.server.url}\n{app.server.token}\n")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        app.stop()


if __name__ == "__main__":
    main()
