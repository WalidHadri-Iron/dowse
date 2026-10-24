"""The local connection between the interface and the app: HTTP on 127.0.0.1, a random port, one token per run.

The window loads `/?t=<token>`; the page trades the token for a cookie (`/api/session`: HttpOnly, SameSite=Strict),
which every later request carries, pictures and media included. A request without it, or for another host name (DNS
rebinding), is refused. Static files (the built interface) need no token: they hold nothing private.

Routes are plain functions registered with `route`; they take the request and return a dict (sent as JSON), bytes
with a content type, a `Stream` (a file, with byte ranges for media), or raise `Refused`. `events` pushes named
events to every open page (Server-Sent Events). A file sent as the body (`application/octet-stream`, its name in
`?name=`: one dropped on the window) is written to `uploads` and handed to the route as `Request.upload`, then
deleted once it's answered.
"""

import hashlib
import http.server
import json
import logging
import mimetypes
import os
import queue
import re
import secrets
import socketserver
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .. import paths

log = logging.getLogger("dowse.server")

STATIC = Path(__file__).with_name("static")
MAX_UPLOAD = 2 << 30   # a file sent whole (dropped on the window): bigger ones are asked for by their path instead


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Upload:
    """A file sent as a request's body: where it was written (with its own name), and its SHA-256."""
    path: Path
    digest: str


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    body: dict
    headers: dict = field(default_factory=dict)
    upload: Upload | None = None


@dataclass
class Stream:
    """A file to send whole or in part (Range): media plays from any moment."""
    path: str
    content_type: str | None = None


@dataclass
class Raw:
    data: bytes
    content_type: str
    cache: str = "no-store"


class Events:
    """Server-Sent Events: each open page gets every event published after it connected."""

    def __init__(self):
        self._queues: list[queue.Queue] = []
        self._lock = threading.Lock()

    def publish(self, name: str, data) -> None:
        message = f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
        with self._lock:
            for q in self._queues:
                q.put(message)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)


class _HTTP(http.server.ThreadingHTTPServer):
    request_queue_size = 128   # Explorer starts one launch per selected file, all at once

    def server_bind(self):
        """Binds without looking up the machine's name: http.server's own bind asks for 127.0.0.1's fully qualified
        name (socket.getfqdn), which on a Mac goes out on the local network (Bonjour) and makes macOS ask the person
        to allow Dowse to find devices on their network. The name is never used."""
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        """A page that let go of a request (a picture scrolled away, a player seeking) isn't news; anything else
        is logged."""
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        log.exception("serving %s", client_address)


class Server:
    def __init__(self, static: Path = STATIC, uploads: Path | None = None):
        self.token = secrets.token_urlsafe(24)
        self.static = static
        self.uploads = uploads or paths.index() / "Dropped"   # beside the index: encrypted with it
        self.events = Events()
        self.routes: dict[tuple[str, str], Callable[[Request], object]] = {}
        self.on_request: Callable[[], None] = lambda: None   # each request from the interface (not its event stream)
        self._http = _HTTP(("127.0.0.1", 0), self._handler())
        self._http.daemon_threads = True
        self.port = self._http.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def page(self, route: str = "") -> str:
        """The address a window opens: the token, and where in the interface (`#/library`)."""
        return f"{self.url}?t={self.token}" + (f"#{route}" if route else "")

    def route(self, method: str, path: str):
        def register(handler):
            self.routes[(method, path)] = handler
            return handler
        return register

    def start(self) -> None:
        threading.Thread(target=self._http.serve_forever, name="server", daemon=True).start()

    def stop(self) -> None:
        self._http.shutdown()

    def _handler(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):   # noqa: D401 (quiet: requests aren't news)
                pass

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

            def _serve(self, method: str) -> None:
                parts = urlsplit(self.path)
                host = self.headers.get("Host", "")
                if host not in (f"127.0.0.1:{server.port}", f"localhost:{server.port}"):
                    return self._send(421, b"wrong host", "text/plain")
                if not parts.path.startswith("/api/"):
                    return self._static(parts.path)
                query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
                if parts.path == "/api/session":
                    return self._session(query.get("t", ""))
                if not secrets.compare_digest(self._cookie(), server.token):
                    return self._send(403, b"no session", "text/plain")
                if parts.path == "/api/events":
                    return self._events()
                handler = server.routes.get((method, parts.path))
                # A person using Dowse makes reading files wait (foreground.py): not a launch from outside, nor a refresh
                # the page made by itself after indexing (X-Dowse-Background), which would slow the very reading it follows.
                if handler is not None and parts.path != "/api/request" and self.headers.get("X-Dowse-Background") != "1":
                    server.on_request()
                if handler is None:
                    return self._send(404, b"no such route", "text/plain")
                body, upload = {}, None
                if method == "POST" and self.headers.get("Content-Type", "").startswith("application/octet-stream"):
                    try:
                        upload = self._upload(query.get("name", ""))
                    except Refused as refused:
                        self.close_connection = True   # what wasn't read of the body stays unread
                        return self._json(refused.status, {"error": str(refused)})
                elif method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    try:
                        body = json.loads(self.rfile.read(length) or b"{}")
                    except ValueError:
                        return self._send(400, b"bad json", "text/plain")
                try:
                    result = handler(Request(method, parts.path, query, body, dict(self.headers), upload))
                except Refused as refused:
                    return self._json(refused.status, {"error": str(refused)})
                except Exception as error:   # a bug: said, logged, and the app goes on
                    log.exception("%s %s", method, parts.path)
                    return self._json(500, {"error": f"{type(error).__name__}: {error}"})
                finally:
                    if upload is not None:
                        shutil.rmtree(upload.path.parent, ignore_errors=True)
                if isinstance(result, Stream):
                    return self._file(result)
                if isinstance(result, Raw):
                    return self._send(200, result.data, result.content_type, cache=result.cache)
                return self._json(200, result if result is not None else {"ok": True})

            # Pieces

            def _upload(self, name: str) -> Upload:
                """The body, written to a folder of its own under its own name (its kind is in its extension)."""
                length = int(self.headers.get("Content-Length") or 0)
                name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(name.replace("\\", "/")).name).strip(" .")
                if not name:
                    raise Refused(400, "The file needs its name")
                if length > MAX_UPLOAD:
                    raise Refused(413, "Too big to drop here. Right-click it in Explorer, then Dowse › Find similar.")
                server.uploads.mkdir(parents=True, exist_ok=True)
                folder = Path(tempfile.mkdtemp(dir=server.uploads))
                target, digest, left = folder / name, hashlib.sha256(), length
                try:
                    with open(target, "wb") as out:
                        while left > 0:
                            chunk = self.rfile.read(min(left, 1 << 20))
                            if not chunk:
                                raise Refused(400, "The file didn't arrive whole")
                            out.write(chunk)
                            digest.update(chunk)
                            left -= len(chunk)
                except BaseException:
                    shutil.rmtree(folder, ignore_errors=True)
                    raise
                return Upload(target, digest.hexdigest())

            def _cookie(self) -> str:
                for part in (self.headers.get("Cookie") or "").split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == "dowse":
                        return value
                return ""

            def _session(self, token: str) -> None:
                if not secrets.compare_digest(token, server.token):
                    return self._send(403, b"wrong token", "text/plain")
                self.send_response(200)
                self.send_header("Set-Cookie", f"dowse={token}; Path=/; HttpOnly; SameSite=Strict")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "11")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def _json(self, status: int, data) -> None:
                self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

            def _send(self, status: int, data: bytes, content_type: str, cache: str = "no-store") -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", cache)
                self.end_headers()
                self.wfile.write(data)

            def _static(self, path: str) -> None:
                relative = path.lstrip("/") or "index.html"
                target = (server.static / relative).resolve()
                if not target.is_relative_to(server.static.resolve()) or not target.is_file():
                    target = server.static / "index.html"   # the interface routes itself (#/library)
                if not target.is_file():
                    return self._send(503, b"the interface isn't built (web/: npm run build)", "text/plain")
                kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                cache = "no-cache" if target.name == "index.html" else "max-age=31536000, immutable"
                self._send(200, target.read_bytes(), kind, cache=cache)

            def _file(self, stream: Stream) -> None:
                size = os.path.getsize(stream.path)
                kind = stream.content_type or mimetypes.guess_type(stream.path)[0] or "application/octet-stream"
                start, end = 0, size - 1
                ranged = self.headers.get("Range", "")
                if ranged.startswith("bytes="):
                    first, _, last = ranged[6:].split(",")[0].partition("-")
                    if first:
                        start, end = int(first), int(last) if last else size - 1
                    elif last:
                        start = max(size - int(last), 0)
                    end = min(end, size - 1)
                    if start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                self.send_response(206 if ranged else 200)
                self.send_header("Content-Type", kind)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if ranged:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with open(stream.path, "rb") as handle:
                    handle.seek(start)
                    left = end - start + 1
                    try:
                        while left > 0:
                            chunk = handle.read(min(left, 1 << 16))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                    except (ConnectionError, OSError):
                        pass   # the player let go (seeking): normal

            def _events(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q = server.events.subscribe()
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            message = q.get(timeout=15)
                        except queue.Empty:
                            message = b": still here\n\n"
                        self.wfile.write(message)
                        self.wfile.flush()
                except (ConnectionError, OSError):
                    pass
                finally:
                    server.events.unsubscribe(q)
                    self.close_connection = True

        return Handler
