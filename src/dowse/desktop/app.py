"""The app: what the interface asks for (routes on the local server), what it's told (events), and everything
between: what Dowse searches (one `Selection`, changed in one place: `choose`), live indexing, search, settings, the
first run, and requests from outside (Explorer's menu, Send to, the command line, a second launch).

The windows, the tray and the shortcut are `native`'s; this module never draws anything.
"""

import contextlib
import hashlib
import json
import logging
import collections
import os
import threading
import time
from datetime import datetime
from pathlib import PurePath
from urllib.parse import urlencode

from .. import aisles, catalog, dates, explore, foreground, modelfiles, paths, privacy, runtime, search_controls, search_scope, searchtext, settings
from .. import __version__
from ..history import History
from . import shell, winapi
from ..fmt import clock
from ..rules import Kind, Skip, cloud_only, attributes, classify
from ..searcher import Hit, text_of, strong_count
from ..selection import Selection
from ..store import Store, default_file, writer_lock
from . import setup, system
from .engine import SearchEngine, Unreadable
from .indexing import Config, Indexing, Status
from .pictures import VISUAL, Pictures
from .screenshots import ScreenshotWatcher, folders as screenshot_folders
from .server import Raw, Refused, Request, Server, Stream

log = logging.getLogger("dowse.app")
OVERVIEW_PIXELS = 96   # the picture size Explore asks for when its whole floor is in view (MapFloor.loadVisible)
MAP_REFRESH = 30.0     # seconds: while files are being read, Explore's map is made again at most this often


def place(kind: Kind, loc: float | None) -> str | None:
    """Where in a file: "Page 12", "Passage 4", "1:20"."""
    if loc is None:
        return None
    if kind is Kind.PDF:
        return f"Page {int(loc)}"
    if kind is Kind.DOC:
        return f"Passage {int(loc) + 1}"
    return clock(loc)


def folder_text(path: str) -> str:
    """The file's folder as people say it: its last two folders, "Documents › Contracts"."""
    parts = PurePath(path).parent.parts
    home = PurePath(os.path.expanduser("~")).parts
    if parts[:len(home)] == home and len(parts) > len(home):
        parts = parts[len(home):]
    return " › ".join(parts[-2:]) if parts else str(PurePath(path).parent)


def hit_json(hit: Hit, rank: int, strong: int) -> dict:
    return {"path": hit.path, "name": PurePath(hit.path).name, "folder": folder_text(hit.path), "kind": hit.kind.value,
            "rank": rank, "strong": rank < strong, "score": round(hit.score, 4), "loc": hit.loc, "locEnd": hit.loc_end,
            "place": place(hit.kind, hit.loc), "excerpt": hit.excerpt, "mtime": hit.mtime, "size": hit.size,
            "info": hit.info, "segment": hit.segment_id, "visual": hit.kind in VISUAL,
            "evidence": [{**e, "place": place(hit.kind, e.get("loc")) + "–" + place(hit.kind, e["locEnd"])
                          if hit.kind in (Kind.AUDIO, Kind.VIDEO) and e.get("loc") is not None and e.get("locEnd") is not None
                          else place(hit.kind, e.get("loc"))} for e in hit.evidence],
            "moments": [{"loc": a, "locEnd": b, "place": place(hit.kind, a)} for a, b in hit.moments[:6]]}


def entry_json(entry: catalog.Entry) -> dict:
    return {"path": entry.path, "name": entry.name, "folder": folder_text(entry.path), "parent": entry.parent,
            "kind": entry.kind.value if entry.kind else None, "size": entry.size, "mtime": entry.mtime,
            "status": entry.status, "statusText": entry.status_text, "detail": entry.detail, "named": entry.named,
            "isFolder": entry.folder, "visual": entry.kind in VISUAL if entry.kind else False,
            "active": entry.active, "inactiveBy": entry.inactive_by}


def choice_message(paths_: list[str], refused: dict[str, str], added: bool, changed: bool) -> tuple[str, str] | None:
    """What a notification says about an add or a remove: "Added 12 files to Dowse", with what couldn't be added."""
    folders = [p for p in paths_ if os.path.isdir(p)]
    files = [p for p in paths_ if not os.path.isdir(p) and p not in refused]
    parts = []
    if len(folders) == 1:
        parts.append(f"“{os.path.basename(folders[0].rstrip(chr(92) + '/')) or folders[0]}”")
    elif folders:
        parts.append(f"{len(folders)} folders")
    if files:
        parts.append(f"{len(files)} file{'s' if len(files) != 1 else ''}")
    what = " and ".join(parts)
    note = ""
    if refused:
        name, why = next(iter(refused.items()))
        more = f" and {len(refused) - 1} more" if len(refused) > 1 else ""
        note = f"Can't add {os.path.basename(name)}{more}: {why}."
    if not what:
        return ("Nothing added", note) if refused else None
    if not changed:
        return ("Already in Dowse" if added else "Not in Dowse", f"{what[0].upper()}{what[1:]} " +
                ("was already searched." if added else "wasn't searched.") + (" " + note if note else ""))
    if added:
        return f"Added {what} to Dowse", "Searchable in a moment, as Dowse reads them." + (" " + note if note else "")
    return f"Removed {what} from Dowse", "Your files aren't touched: Dowse just stops searching them."


class App:
    def __init__(self, native_factory=None):
        self.settings = settings.load()
        self.model = modelfiles.get()
        if self.settings.get("search_model", modelfiles.DEFAULT) != self.model.id:
            self._save(search_model=self.model.id)   # recover the index's committed model after an interrupted switch
        self._model_lock = threading.RLock()
        self._theme_lock = threading.RLock()
        self._closing = False
        self.selection = Selection.from_settings(self.settings)
        self.choice = None if setup.needed() else runtime.load()
        self.server = Server()
        self.server.on_request = foreground.touch   # the person first: reading files steps aside while they use Dowse
        self.events = self.server.events
        self.pictures = Pictures()
        self.engine = self._engine(self.choice)
        self.history = History()
        self.screenshots: ScreenshotWatcher | None = None   # new screenshots added by name, while the switch is on
        self.indexing = Indexing(self._config(), on_status=self._status, on_changed=self._index_changed,
                                 on_chosen=self._chosen)
        self._lock = threading.Lock()
        self._announced = False
        self._incoming: dict[str, list[str]] = {"add": [], "remove": []}
        self._gather: threading.Timer | None = None
        self._job: setup.SetupJob | None = None
        self._setup_progress: dict | None = None
        self._model_progress: dict | None = None
        self._counter = setup.Counter(lambda folder, counts: self.events.publish("counted", {
            "folder": folder, "counts": counts}))
        self._theme = self._read_theme()
        self._map: dict | None = None   # Explore's map (and who's in which aisle)
        self._map_generation = 0          # bumped when your aisles change: a map made before is no longer right
        self._map_stale = False           # files were read since it was made: made again in the background
        self._map_building = False
        self._map_made = 0.0
        self._map_lock = threading.Lock()
        self._vectors = None   # (file ids, vectors) for Explore and the aisles, until the index changes
        self._vectors_lock = threading.Lock()
        self.native = native_factory(self) if native_factory else None
        self._routes()

    # Pieces

    def _engine(self, choice) -> SearchEngine:
        return SearchEngine(choice, on_state=lambda state: self.events.publish("model", {"state": state}), model=self.model)

    def _config(self) -> Config:
        return Config(self.choice, self.selection, bool(self.settings.get("plugged_in_only", False)),
                      Skip.from_settings(self.settings), self.model.id)

    def _save(self, **changes) -> None:
        self.settings.update(changes)
        settings.save(self.settings)

    def begin(self) -> None:
        """Searching and indexing, once there's a model and something chosen (from whichever came last)."""
        if not (self.selection.folders or self.selection.files):
            if self.selection.forgotten or self.selection.removed:
                config = self._config()
                if config != self.indexing.config:
                    self.indexing.configure(config)
                elif not self.indexing.started:
                    self.indexing.start()
            return
        if self.choice is None:
            return
        if self.engine.choice != self.choice or self.engine.model != self.model:
            old, self.engine = self.engine, self._engine(self.choice)
            old.stop()
        config = self._config()
        if config != self.indexing.config:
            self.indexing.configure(config)
        elif not self.indexing.started:
            self.indexing.start()

    def start(self, announce: bool = True) -> None:
        """`announce`: tell later launches where to find this app (`app.json`); a development server doesn't."""
        self.server.start()
        if announce:
            (paths.home() / "app.json").write_text(json.dumps({"port": self.server.port, "token": self.server.token,
                                                                "pid": os.getpid()}), encoding="utf-8")
            self._announced = True
        if announce and self.settings.get("explorer_menu", True):
            shell.keep_registered()        # re-pointed if the launcher moved (a new version, a new Python)
        if announce and winapi.starts_with_windows():
            winapi.set_starts_with_windows(True)   # likewise for starting with Windows
        threading.Thread(target=self._watch_theme, name="theme", daemon=True).start()
        self.begin()
        if announce:   # (a development server shares the settings: the app alone adds screenshots)
            self._screenshot_switch()

    def _screenshot_switch(self) -> None:
        """New screenshots are added as they're saved while the setting is on; nothing is watched while it's off."""
        on = bool(self.settings.get("screenshots", False))
        if on and self.screenshots is None:
            self.screenshots = ScreenshotWatcher(self._new_screenshots)
            try:
                self.screenshots.start()
            except OSError:
                log.warning("can't watch for screenshots", exc_info=True)
                self.screenshots = None
        elif not on and self.screenshots is not None:
            self.screenshots.stop()
            self.screenshots = None

    def _new_screenshots(self, paths_: list[str]) -> None:
        log.info("new screenshots: %s", len(paths_))
        self.add_paths(paths_, quiet=True)   # (one each time: no notification, they're in Dowsed files)
        self.events.publish("screenshots", {"paths": paths_})

    def stop(self) -> None:
        if self.screenshots is not None:
            self.screenshots.stop()
            self.screenshots = None
        with self._model_lock:
            self._closing = True
            if self._job:
                self._job.cancel()
            self.indexing.close()
            self.engine.stop()
        if self._announced:
            with contextlib.suppress(OSError, ValueError):
                address = paths.home() / "app.json"
                owner = json.loads(address.read_text(encoding="utf-8"))
                if owner.get("pid") == os.getpid() and owner.get("token") == self.server.token:
                    address.unlink()

    def _status(self, status: Status) -> None:
        self.events.publish("status", status.json())
        if self.native:
            self.native.status(status)

    def _aisles_changed(self) -> None:
        """Your aisles changed: Explore's map is made again on its next look (with the change: never an old one)."""
        with self._map_lock:
            self._map = None
            self._map_generation += 1
        self.events.publish("aislesChanged", {})

    def _current_map(self) -> dict:
        """Explore's map. Made on the first look (and after your aisles change); after files are read, the one there
        is shown while a new one is made in the background, at most every `MAP_REFRESH` seconds (`_map_soon`): made
        for every step of reading, it kept the app busy and every look waited for it (1 to 7 s, 10,000 files)."""
        with self._map_lock:
            if self._map is None:
                self._map = self._build_map()
                self._map_made = time.monotonic()
                self._map_stale = False
                threading.Thread(target=self._picture_overview, args=(self._map,), daemon=True,
                                 name="explore-pictures").start()
            return self._map

    def _map_soon(self) -> None:
        """The index changed: a fresh map in the background (if one was ever wanted), when `MAP_REFRESH` allows."""
        with self._map_lock:
            self._map_stale = True
            if self._map is None or self._map_building:
                return
            self._map_building = True
            delay = max(0.0, self._map_made + MAP_REFRESH - time.monotonic())
        timer = threading.Timer(delay, self._remake_map)
        timer.daemon = True
        timer.start()

    def _remake_map(self) -> None:
        try:
            with self._map_lock:
                generation, self._map_stale = self._map_generation, False
            if self._closing:
                return
            built = self._build_map()   # (outside the lock: looks get the map there meanwhile)
            with self._map_lock:
                if self._map is not None and generation == self._map_generation:
                    self._map, self._map_made = built, time.monotonic()
                    threading.Thread(target=self._picture_overview, args=(built,), daemon=True,
                                     name="explore-pictures").start()
            self.events.publish("mapChanged", {})
        except Exception:
            log.exception("making Explore's map")
        finally:
            with self._map_lock:
                self._map_building = False
                again = self._map_stale
            if again:
                self._map_soon()

    def _build_map(self) -> dict:
        defined = aisles.definitions(self.settings)
        reader = Store(default_file())
        try:
            embedder = self.engine.embedder(wait=10.0)
            vectors = self._file_vectors(reader)
            placement = self._place(reader, embedder, defined, vectors) if defined else None
            built = explore.build(reader, embedder, defined, placement,
                                  auto=bool(self.settings.get("aisles_auto", True)),
                                  dismissed=aisles.dismissals(self.settings),
                                  vectors=vectors)
            # Match search's capture-date/mtime rule in the server's local timezone.
            # Send calendar dates so the floor can filter the entire map, not a capped hit list.
            file_dates = {}
            for path, mtime, info in reader.db.execute("SELECT path, mtime, info FROM files"):
                stamp = dates.taken(json.loads(info or "{}").get("taken")) or mtime
                file_dates[path] = datetime.fromtimestamp(stamp).date().isoformat()
            built["kinds"] = {path: kind for path, kind in reader.db.execute("SELECT path, kind FROM files")}
        finally:
            reader.close()
        for piece in built["pieces"]:
            kind = Kind(piece["kind"])
            piece.update(name=PurePath(piece["path"]).name, folder=folder_text(piece["path"]),
                         visual=kind in VISUAL, fileDate=file_dates.get(piece["path"]))
        return built

    def _file_vectors(self, reader: Store):
        """Every file's vector for Explore and the aisles, kept until the index changes (a preview per keystroke
        shouldn't read every vector again)."""
        with self._vectors_lock:
            if self._vectors is None:
                self._vectors = explore.files(reader)
            return self._vectors

    def _place(self, reader: Store, embedder, defined: list, vectors) -> aisles.Placement:
        ids = dict((path, file) for file, path in reader.db.execute("SELECT id, path FROM files"))
        kinds = dict(reader.db.execute("SELECT id, kind FROM files"))
        return aisles.place(defined, explore.standing(reader, embedder, defined, vectors, search=self.engine.fit),
                            ids, kinds)

    def _kinds(self) -> list[tuple[str, str]]:
        reader = Store(default_file())
        try:
            return reader.db.execute("SELECT path, kind FROM files").fetchall()
        finally:
            reader.close()

    def _index_changed(self) -> None:
        self.engine.reload()
        with self._vectors_lock:
            self._vectors = None
        self._map_soon()
        self.events.publish("indexChanged", {})

    def _chosen(self, summary) -> None:
        log.info("selection applied: %s added, %s removed", summary.added, summary.removed)
        self._index_changed()

    def _theme_mode(self) -> str:
        mode = self.settings.get("theme", "system")
        return mode if mode in ("system", "light", "dark") else "system"

    def _read_theme(self) -> dict:
        look = system.theme()
        mode = self._theme_mode()
        return {**look, "dark": look["dark"] if mode == "system" else mode == "dark"}

    def _update_theme(self) -> None:
        with self._theme_lock:
            look = self._read_theme()
            if look != self._theme:
                self._theme = look
                self.events.publish("theme", look)
                if self.native:
                    self.native.theme(look)

    def _watch_theme(self) -> None:
        while not self._closing:
            threading.Event().wait(2.0)
            if not self._closing:
                self._update_theme()

    def notify(self, message: tuple[str, str] | None) -> None:
        if message and self.native:
            self.native.notify(*message)

    # What Dowse searches: the one place it changes

    def choose(self, selection: Selection) -> None:
        with self._model_lock:
            self._choose(selection)

    def _choose(self, selection: Selection) -> None:
        if selection == self.selection:
            return
        saved = selection.to_settings(self.settings)
        settings.save(saved)
        self.selection, self.settings = selection, saved
        if self.indexing.started:
            self.indexing.configure(self._config())
        else:
            self.begin()
        self._index_changed()
        self.events.publish("selection", self._selection_json())

    def add_paths(self, paths_: list[str], quiet: bool = False) -> dict:
        with self._model_lock:
            return self._add_paths(paths_, quiet)

    def _add_paths(self, paths_: list[str], quiet: bool = False) -> dict:
        paths_ = list(dict.fromkeys(os.path.abspath(p) for p in paths_ if os.path.exists(p)))
        before = self.selection
        after, refused = catalog.add(before, paths_, self._config().skip)
        self.choose(after)
        if not quiet:
            self.notify(choice_message(paths_, refused, added=True, changed=after != before))
        return {"before": self._selection_json(before), "changed": after != before, "refused": refused}

    def remove_paths(self, paths_: list[str], quiet: bool = False) -> dict:
        with self._model_lock:
            return self._remove_paths(paths_, quiet)

    def _remove_paths(self, paths_: list[str], quiet: bool = False) -> dict:
        paths_ = list(dict.fromkeys(os.path.abspath(p) for p in paths_))
        before = self.selection
        after = before.remove(paths_)
        self.choose(after)
        if not quiet:
            self.notify(choice_message(paths_, {}, added=False, changed=after != before))
        return {"before": self._selection_json(before), "changed": after != before}

    def active_paths(self, paths_: list[str], active: bool) -> dict:
        with self._model_lock:
            paths_ = list(dict.fromkeys(os.path.abspath(p) for p in paths_))
            before = self.selection
            after = before.activate(paths_) if active else before.deactivate(paths_)
            blocked = {p: after.inactive_by(p) for p in paths_ if active and after.inactive_by(p)}
            self.choose(after)
        return {"before": self._selection_json(before), "changed": after != before, "blocked": blocked}

    def _selection_json(self, selection: Selection | None = None) -> dict:
        selection = selection or self.selection
        return {"folders": list(selection.folders), "files": list(selection.files),
                "removed": list(selection.removed), "inactive": list(selection.inactive),
                "forgotten": list(selection.forgotten)}

    # Requests from outside (`launch`): Explorer, Send to, the command line, a second launch

    def request(self, request: dict) -> None:
        op = request.get("op")
        if op in self._incoming:
            with self._lock:
                self._incoming[op] += request.get("paths", [])
                if self._gather:
                    self._gather.cancel()
                self._gather = threading.Timer(0.4, self._apply_incoming)   # one change for the whole selection
                self._gather.start()
        elif op in ("activate", "deactivate"):
            self.active_paths(request.get("paths", []), active=op == "activate")
        elif op == "like" and request.get("paths"):
            self.native and self.native.show_main("/search?" + urlencode({"like": request["paths"][0]}))
        elif op == "search" and request.get("paths"):
            self.native and self.native.show_main("/search?" + urlencode({"in": request["paths"][0]}))
        elif op == "library":
            self.native and self.native.show_main("/library")
        elif op == "settings":
            self.native and self.native.show_main("/settings")
        elif op == "quit":   # an update or an uninstall, about to replace or remove the app's files
            log.info("asked to quit")
            self.native and self.native.quit()
        else:
            self.native and self.native.show_popup()

    def _apply_incoming(self) -> None:
        with self._lock:
            added, removed = self._incoming["add"], self._incoming["remove"]
            self._incoming = {"add": [], "remove": []}
        if added:
            self.add_paths(added)
        if removed:
            self.remove_paths(removed)

    # The state the interface starts from

    def _picture_overview(self, built: dict) -> None:
        """The pictures Explore's whole-floor view asks for (the smallest size), rendered ahead: the floor fills at
        once instead of a case at a time."""
        reader = Store(default_file())
        try:
            rows = {row[0]: row for row in reader.db.execute("SELECT path, kind, mtime FROM files")}
        finally:
            reader.close()
        for piece in built["pieces"]:
            if self._map is not built:
                return   # the index changed: a new map, its own pictures
            row = rows.get(piece["path"])
            if piece["visual"] and row:
                foreground.wait()   # the person first: pictures made ahead step aside while Dowse is used
                self.pictures.get(row[0], Kind(row[1]), row[2], None, OVERVIEW_PIXELS)   # (None when unreadable)

    def gpu_name(self) -> str | None:
        """The graphics card to show: the one Dowse runs on, else the one it would use (Windows' list, this PC's)."""
        if self.choice and self.choice.device:
            return self.choice.name or runtime.planned_gpu() or self.choice.device
        return runtime.planned_gpu()

    def state(self) -> dict:
        gpu = self.gpu_name()
        return {
            "version": __version__,
            "setupNeeded": self.choice is None,
            "searchModel": self.model.json(),
            "device": {"choice": self.choice.describe() if self.choice else None,
                       "onGpu": bool(self.choice and self.choice.device), "gpu": gpu},
            "status": self.indexing.status.json(), "model": self.engine.state,
            "selection": self._selection_json(), "theme": self._theme,
            "settings": {"theme": self._theme_mode(), "hotkey": self.settings["hotkey"], "startup": winapi.starts_with_windows(),
                         "pluggedInOnly": bool(self.settings.get("plugged_in_only", False)),
                         "paused": self.indexing.user_paused,
                         "explorerMenu": bool(self.settings.get("explorer_menu", True)),
                         "screenshots": bool(self.settings.get("screenshots", False)),
                         "screenshotFolders": [f for f in screenshot_folders() if os.path.isdir(f)] or screenshot_folders()[:1],
                         "encrypt": bool(self.settings.get("encrypt_index", False)),
                         "encryptionSupported": privacy.supported(),
                         "history": bool(self.settings.get("history", True)),
                         **Skip.from_settings(self.settings).to_settings()},
        }

    # Routes

    def _routes(self) -> None:
        route = self.server.route

        @route("GET", "/api/state")
        def state(_: Request):
            return self.state()

        @route("POST", "/api/search")
        def search(request: Request):
            body = request.body
            try:
                kinds = frozenset(Kind(k) for k in body.get("kinds", []))
                answer = self.engine.search(body.get("q", ""), kinds, body.get("within"),
                                            int(body.get("limit", 60)), ignored_filters=body.get("ignoredFilters"),
                                            date_range=body.get("date"))
            except ValueError as error:
                raise Refused(400, str(error)) from error
            return self._answer_json(answer)

        @route("POST", "/api/similar")
        def similar(request: Request):
            """More like this: a file of the index, a place in it (`loc`), or a file on disk it doesn't hold."""
            body = request.body
            path = str(body.get("path") or "")
            if not os.path.isabs(path) or not os.path.isfile(path):
                raise Refused(404, "That file isn't there any more.")
            loc = body.get("loc")
            return self._like(path, None if loc in (None, "") else float(loc), body)

        @route("POST", "/api/similar/file")
        def similar_file(request: Request):
            """More like a file sent whole (dropped on the window). If the index holds the same file, it stands for
            itself (its vectors, and it isn't its own neighbour)."""
            if request.upload is None:
                raise Refused(400, "Send the file itself")
            same = self._same_file(request.upload.path, request.upload.digest)
            try:
                within = json.loads(request.query["folders"]) if request.query.get("folders") else None
            except ValueError as error:
                raise Refused(400, "Folders must be a list") from error
            answer = self._like(same or str(request.upload.path), None, {
                "kinds": [k for k in request.query.get("kinds", "").split(",") if k], "within": within,
                "date": {"from": request.query.get("from", ""), "to": request.query.get("to", "")}
                if request.query.get("from") or request.query.get("to") else None},
                key=("dropped", request.upload.digest))
            if not same:
                answer["like"] |= {"path": None, "indexed": False}
            return answer

        @route("GET", "/api/history")
        def history_(request: Request):
            """Files gone back to lately, and earlier searches starting as `q` does, each with what was opened."""
            if not self.settings.get("history", True):
                return {"recent": [], "queries": []}
            reader = Store(default_file())
            try:
                live = self._live(reader)
                recent = self.history.recent(min(int(request.query.get("limit", 8)), 50), keep=live)
                queries = self.history.queries(request.query.get("q", ""), 5, keep=live)
                found = {hit["path"]: hit for hit in self._file_hits(reader, recent + [q["path"] for q in queries])}
            finally:
                reader.close()
            return {"recent": [found[p] for p in recent if p in found],
                    "queries": [{"query": q["query"], "exact": q["exact"], "at": q["at"], "file": found.get(q["path"])}
                                for q in queries]}

        @route("POST", "/api/history/clear")
        def history_clear(_: Request):
            self.history.forget()

        @route("GET", "/api/search/folders")
        def search_folders(_: Request):
            reader = Store(default_file())
            try:
                return {"folders": catalog.search_folders(reader, self.selection)}
            finally:
                reader.close()

        @route("GET", "/api/recent")
        @route("GET", "/api/browse")
        def recent(request: Request):
            """Browse the whole searchable collection in bounded pages; recent remains a small context preview."""
            browsing = request.path == "/api/browse"
            try:
                limit = min(max(int(request.query.get("limit", 80 if browsing else 40)), 1), 500 if browsing else 200)
                offset = int(request.query.get("offset", 0)) if browsing else 0
                if offset < 0:
                    raise ValueError("Page offset cannot be negative")
                within = search_scope.folders(json.loads(request.query["folders"]) if "folders" in request.query
                                             else request.query.get("in") or None)
                kinds = {Kind(k) for k in request.query.get("kinds", "").split(",") if k}
                date_range = {"from": request.query.get("from", ""), "to": request.query.get("to", "")} \
                    if "from" in request.query or "to" in request.query else None
                span, _ = search_controls.resolve("", frozenset(kinds), within, [], date_range)
            except ValueError as error:
                raise Refused(400, str(error)) from error
            reader = Store(default_file())
            try:
                rows = reader.db.execute(
                    "SELECT f.path, f.kind, f.size, f.mtime, f.info, (SELECT s.id FROM segments s WHERE s.file = f.id "
                    "AND s.kind IN ('chunk', 'page') ORDER BY s.id LIMIT 1) FROM files f "
                    "WHERE f.active = 1 AND f.state IN ('done', 'partial') ORDER BY f.mtime DESC" +
                    (", f.id DESC" if browsing else "") + " LIMIT ?",
                    (-1 if browsing or within or kinds or span else limit * 3,))
                matched = []
                for path, kind, size_, mtime, info, segment in rows:
                    if self.selection.decide(path) is None or not search_scope.contains(path, within):
                        continue
                    kind = Kind(kind)
                    if kinds and kind not in kinds:
                        continue
                    details = json.loads(info or "{}")
                    if span and not span.start <= (dates.taken(details.get("taken")) or mtime) < span.end:
                        continue
                    matched.append((path, kind, size_, mtime, details, segment))
                    if not browsing and len(matched) >= limit:
                        break
                total = len(matched)
                offset = min(offset, max(0, (total - 1) // limit * limit))
                out = []
                for path, kind, size_, mtime, details, segment in matched[offset:offset + limit]:
                    head = (text_of(reader, segment) or "")[:320] if segment else ""
                    out.append({"path": path, "name": PurePath(path).name, "folder": folder_text(path), "kind": kind.value,
                                "rank": len(out), "strong": True, "score": 0, "loc": None, "locEnd": None, "place": None,
                                "excerpt": " ".join(head.split()) or None, "mtime": mtime, "size": size_, "info": details,
                                "segment": segment or 0, "visual": kind in VISUAL, "moments": []})
            finally:
                reader.close()
            if browsing:
                return {"hits": out, "total": total, "offset": offset, "limit": limit,
                        "hasMore": offset + len(out) < total}
            return {"hits": out}

        @route("GET", "/api/explore")
        def explore_map(_: Request):
            # The writer applies switches between reading steps; don't leak its old map meanwhile.
            built = {k: v for k, v in self._current_map().items() if k not in ("membership", "near", "kinds", "groups")}
            visible = [p for p in built["pieces"] if self.selection.decide(p["path"]) is not None]
            if len(visible) == len(built["pieces"]):
                return built
            counts = collections.Counter(p["aisle"] for p in visible)
            return {**built, "pieces": visible,
                    "aisles": [{**a, "count": counts[a["index"]]} for a in built["aisles"] if counts[a["index"]]],
                    "total": len(visible)}

        # Aisles: yours (a name, what belongs, pinned and kept-out files) and suggestions (the automatic ones)

        def item(path: str, **extra) -> dict:
            kind = self._map.get("kinds", {}).get(path) if self._map else None
            return {"path": path, "name": PurePath(path).name, "folder": folder_text(path), "kind": kind,
                    "visual": Kind(kind) in VISUAL if kind else False, **extra}

        def overview() -> dict:
            built = self._current_map()   # the map holds who's in which aisle
            visible = lambda paths_: [p for p in paths_ if self.selection.decide(p) is not None]   # noqa: E731
            mine = []
            for definition in aisles.definitions(self.settings):
                members = visible(built["membership"].get(definition.id, []))
                mine.append({**definition.json(), "count": len(members), "samples": members[:6]})
            suggestions = [{"id": g["id"], "label": g["label"], "about": g["about"], "count": len(visible(g["members"])),
                            "samples": visible(g["members"])[:6]}
                           for g in built["groups"] if not g["dismissed"] and visible(g["members"])]
            return {"aisles": mine, "suggestions": suggestions, "auto": bool(self.settings.get("aisles_auto", True))}

        @route("GET", "/api/aisles")
        def aisles_list(_: Request):
            return overview()

        @route("GET", "/api/aisles/members")
        def aisle_members(request: Request):
            built = self._current_map()
            wanted = request.query.get("id", "")
            definition = next((d for d in aisles.definitions(self.settings) if d.id == wanted), None)
            pinned = set(definition.pinned) if definition else set()
            group = None if definition else next((g for g in built.get("groups", []) if g["id"] == wanted), None)
            members = [p for p in (group["members"] if group else built["membership"].get(wanted, []))
                       if self.selection.decide(p) is not None]   # (a suggestion: the files Dowse grouped)
            return {"members": [item(p, pinned=p in pinned) for p in members],
                    "near": [item(p) for p in built["near"].get(wanted, [])
                             if self.selection.decide(p) is not None][:30],
                    "excluded": [item(p) for p in (definition.excluded if definition else [])]}

        @route("POST", "/api/aisles/preview")
        def aisle_preview(request: Request):
            """What an aisle being edited would hold, among the others as they are: nothing is saved."""
            try:
                draft = aisles.Definition.from_json({**request.body, "id": request.body.get("id") or "draft"})
            except ValueError as error:
                raise Refused(400, str(error)) from error
            others = [d for d in aisles.definitions(self.settings) if d.id != draft.id]
            reader = Store(default_file())
            try:
                ids = dict((path, file) for file, path in reader.db.execute("SELECT id, path FROM files WHERE active = 1"))
                placement = self._place(reader, self.engine.embedder(wait=10.0), [*others, draft],
                                        self._file_vectors(reader))
            finally:
                reader.close()
            path = {file: p for p, file in ids.items()}
            members = [path[f] for f in placement.members(draft.id) if f in path]
            members = [p for p in members if self.selection.decide(p) is not None]
            near = [path[f] for f in placement.near.get(draft.id, []) if f in path]
            kinds = dict(self._kinds())
            describe = lambda p, **extra: {"path": p, "name": PurePath(p).name, "folder": folder_text(p),   # noqa: E731
                                           "kind": kinds.get(p), "visual": Kind(kinds[p]) in VISUAL if kinds.get(p)
                                           else False, **extra}
            return {"count": len(members),
                    "members": [describe(p, pinned=p in draft.pinned) for p in members[:120]],
                    "near": [describe(p) for p in near if self.selection.decide(p) is not None][:30]}

        @route("POST", "/api/aisles/save")
        def aisle_save(request: Request):
            try:
                definition = aisles.Definition.from_json(request.body)
            except ValueError as error:
                raise Refused(400, str(error)) from error
            self._save(aisles=aisles.saved(self.settings, definition))
            self._aisles_changed()
            return definition.json()

        @route("POST", "/api/aisles/delete")
        def aisle_delete(request: Request):
            wanted = request.body.get("id")
            self._save(aisles=[d.json() for d in aisles.definitions(self.settings) if d.id != wanted])
            self._aisles_changed()

        @route("POST", "/api/aisles/auto")
        def aisles_auto(request: Request):
            """Whether what your aisles don't take is grouped into aisles of its own, or all in "Everything else"."""
            self._save(aisles_auto=bool(request.body.get("on", True)))
            self._aisles_changed()

        @route("POST", "/api/aisles/dismiss")
        def aisle_dismiss(request: Request):
            label = str(request.body.get("label", ""))
            group = next((g for g in (self._map or {}).get("groups", []) if g["label"] == label), None)
            if label:   # kept with its files: grouped again under another name, it's still the one dismissed
                gone = {"label": label, "members": group["members"][:500] if group else []}
                self._save(aisles_dismissed=[*self.settings.get("aisles_dismissed", []), gone][-100:])
                self._aisles_changed()

        @route("GET", "/api/facts")
        def facts(request: Request):
            reader = Store(default_file())
            try:
                if request.query.get("quick"):   # the rail's: its size alone (counting vectors reads every one)
                    return {"bytes": reader.size_on_disk()}
                files = reader.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                return {"files": files, "activeFiles": reader.progress()[1], "vectors": reader.vector_count(), "bytes": reader.size_on_disk(),
                        "index": str(default_file().parent), "logs": str(paths.logs())}
            finally:
                reader.close()

        @route("POST", "/api/folder")
        def open_folder(request: Request):
            target = request.body.get("which")
            system.open_folder(str(default_file().parent if target == "index" else paths.logs()))

        @route("POST", "/api/warm")
        def warm(_: Request):
            self.engine.warm()
            return {"model": self.engine.state}

        @route("GET", "/api/picture")
        def picture(request: Request):
            row = self._indexed(request.query.get("path", ""))
            if cloud_only(attributes(row[0])):
                raise Refused(409, "This file is only online. Make it available offline to preview it.")
            terms = tuple(t for t in request.query.get("terms", "").split(",") if t)
            loc = float(request.query["loc"]) if request.query.get("loc") not in (None, "", "null") else None
            pixels = min(max(int(request.query.get("px", 480)), 64), 2048)
            data = self.pictures.get(row[0], Kind(row[1]), row[2], loc, pixels, terms,
                                     whole=request.query.get("whole") == "1")
            if data is None:
                raise Refused(404, "nothing to show")
            return Raw(data, "image/webp", cache="private, max-age=3600")

        @route("GET", "/api/media")
        def media(request: Request):
            row = self._indexed(request.query.get("path", ""))
            if cloud_only(attributes(row[0])):
                raise Refused(409, "This file is only online. Make it available offline to preview it.")
            return Stream(row[0])

        @route("GET", "/api/passage")
        def passage(request: Request):
            segment = int(request.query.get("segment", 0) or 0)
            path, loc, count = request.query.get("path"), request.query.get("loc"), None
            reader = Store(default_file())
            try:
                if path and loc not in (None, ""):   # a document's passage by its number: paging through it
                    row = reader.db.execute(
                        "SELECT s.id FROM segments s JOIN files f ON f.id = s.file WHERE f.path = ? "
                        "AND s.kind IN ('chunk', 'page') AND s.loc = ?", (path, float(loc))).fetchone()
                    segment = row[0] if row else 0
                elif not segment and path:   # no match to show: the file's opening words
                    row = reader.db.execute(
                        "SELECT s.id FROM segments s JOIN files f ON f.id = s.file WHERE f.path = ? "
                        "AND s.kind IN ('chunk', 'page', 'ocr') ORDER BY s.id LIMIT 1", (path,)).fetchone()
                    segment = row[0] if row else 0
                if path:
                    count = reader.db.execute(
                        "SELECT COUNT(*) FROM segments s JOIN files f ON f.id = s.file WHERE f.path = ? "
                        "AND s.kind IN ('chunk', 'page')", (path,)).fetchone()[0]
                text = text_of(reader, segment) if segment else None
                at = reader.db.execute("SELECT loc FROM segments WHERE id = ?", (segment,)).fetchone() if segment else None
            finally:
                reader.close()
            if not text:
                return {"text": None, "loc": None, "count": count}
            return {"text": searchtext.mark(text.strip(), searchtext.terms(request.query.get("q", ""))),
                    "loc": at[0] if at else None, "count": count}

        @route("POST", "/api/open")
        def open_(request: Request):
            row = self._indexed(request.body.get("path", ""))
            system.open_file(row[0], Kind(row[1]), request.body.get("loc"))
            self._went_back(row[0], request.body.get("q"))

        @route("POST", "/api/reveal")
        def reveal(request: Request):
            system.reveal(request.body["path"])
            self._went_back(request.body["path"], request.body.get("q"))

        @route("POST", "/api/copy")
        def copy(request: Request):
            path = request.body["path"]
            (system.copy_file if request.body.get("as") == "file" else system.copy_path)(path)
            self._went_back(path, request.body.get("q"))

        @route("GET", "/api/library")
        def library(request: Request):
            view = request.query.get("view", catalog.ALL)
            store = Store(default_file())
            try:
                entries = catalog.entries(store, self.selection, view, self._config().skip)
                counts = catalog.counts(store, self.selection, self._config().skip)
            finally:
                store.close()
            return {"view": view, "entries": [entry_json(e) for e in entries], "counts": counts}

        @route("POST", "/api/choose")
        def choose(request: Request):
            body = request.body
            op = body.get("op")
            if op == "add":
                return self.add_paths(body.get("paths", []), quiet=True)
            if op == "remove":
                return self.remove_paths(body.get("paths", []), quiet=True)
            if op == "restore":
                chosen = body["selection"]
                self.choose(Selection.from_settings(chosen))
                return {}
            if op in ("activate", "deactivate"):
                return self.active_paths(body.get("paths", []), active=op == "activate")
            if op == "readAgain":
                self.indexing.read_again(body.get("paths", []))
                return {}
            raise Refused(400, f"unknown op {op}")

        @route("POST", "/api/pick")
        def pick(request: Request):
            if not self.native:
                raise Refused(503, "no window")
            return {"paths": self.native.pick(request.body.get("what", "files"))}

        @route("POST", "/api/settings")
        def change(request: Request):
            return self.change_settings(request.body)

        @route("POST", "/api/window")
        def window(request: Request):
            if not self.native:
                return {"native": False}
            action = request.body.get("action")
            if action == "main":
                changed = self.native.show_main(request.body.get("route", "/search"), wait=15)
            elif action == "popup":
                changed = self.native.show_popup(request.body.get("route"), wait=15)
            elif action == "hidePopup":
                self.native.hide_popup()
            elif action == "quit":
                self.native.quit()
            else:
                raise Refused(400, "unknown window action")
            if action in ("main", "popup") and changed is not True:
                why = changed.get("error") if isinstance(changed, dict) else "the window didn't respond"
                raise Refused(503, why)
            return {"native": True}

        @route("POST", "/api/request")
        def outside(request: Request):
            self.request(request.body)

        @route("GET", "/api/setup")
        def setup_state(_: Request):
            folders = []
            for folder in setup.suggestions():
                counts = self._counter.count(folder["path"])
                folders.append({**folder, "counts": counts})
            return {"folders": folders, "progress": self._setup_progress, "gpu": self.gpu_name()}

        @route("GET", "/api/models")
        def models(_: Request):
            store = Store()
            try:
                has_index = bool(store.db.execute("SELECT COUNT(*) FROM files").fetchone()[0])
            finally:
                store.close()
            return {"selected": self.model.id, "models": [m.json() for m in modelfiles.MODELS],
                    "job": self._model_progress, "hasIndex": has_index}

        @route("POST", "/api/models/change")
        def model_change(request: Request):
            model = self._model(request.body.get("model"))
            if self._job and self._job.thread and self._job.thread.is_alive():
                raise Refused(409, "Another model or processor change is in progress")
            if model.id == self.model.id:
                return {"ok": True, "job": self._model_progress, "state": self.state()}
            if request.body.get("confirmRebuild") is not True:
                raise Refused(409, "Changing models rebuilds the search index. Confirm before continuing.")
            self._prepare_model(model, self.choice.backend if self.choice else "auto")
            return {"ok": True, "job": self._model_progress}

        @route("POST", "/api/models/cancel")
        def model_cancel(_: Request):
            with self._model_lock:
                if not self._job or not self._job.cancel():
                    raise Refused(409, "This model preparation has finished or is already activating")
            return {"ok": True}

        @route("POST", "/api/setup/start")
        def setup_start(request: Request):
            model = self._model(request.body.get("model", self.model.id))
            store = Store()
            try:
                tag = store.meta("vectors")
                different = model.id != self.model.id or tag not in (None, model.vector_tag)
                populated = bool(store.db.execute("SELECT COUNT(*) FROM files").fetchone()[0])
            finally:
                store.close()
            if populated and different and request.body.get("confirmRebuild") is not True:
                raise Refused(409, "There is an existing index. Confirm its rebuild before changing the model.")
            self._prepare_model(model, "auto", initial=True)
            return {"ok": True, "job": self._model_progress}

        @route("POST", "/api/setup/count")
        def setup_count(request: Request):
            return {"counts": self._counter.count(request.body["path"])}

        @route("POST", "/api/setup/estimate")
        def setup_estimate(request: Request):
            gpu = self.choice.device is not None if self.choice else runtime.planned_gpu() is not None
            return setup.estimate(request.body.get("counts", {}), self.choice, gpu,
                                  model=self._model(request.body.get("model", self.model.id)))

        @route("POST", "/api/setup/finish")
        def setup_finish(request: Request):
            if self.choice is None:
                raise Refused(409, "Wait until the search model is ready before finishing setup")
            self.selection = Selection(tuple(request.body.get("folders", [])), self.selection.files,
                                       self.selection.removed, self.selection.inactive, self.selection.forgotten)
            self.settings = self.selection.to_settings(self.settings)
            settings.save(self.settings)
            winapi.set_starts_with_windows(bool(request.body.get("startup", True)))
            self.events.publish("selection", self._selection_json())
            self.begin()

    def _answer_json(self, answer) -> dict:
        hits = [h for h in answer.hits if self.selection.decide(h.path) is not None]
        strong = strong_count(hits)
        return {"query": answer.query, "meaning": answer.meaning, "ms": round(answer.milliseconds),
                "strong": strong, "terms": searchtext.terms(dates.for_meaning(answer.query)), "when": answer.when,
                "filters": answer.filters,
                "hits": [hit_json(h, i, strong) for i, h in enumerate(hits)]}

    def _like(self, path: str, loc: float | None, body: dict, key: tuple | None = None) -> dict:
        """More like `path` (or the place `loc` in it), as a search's answer, with what it's like (`like`)."""
        try:
            kinds = frozenset(Kind(k) for k in body.get("kinds") or [])
            answer = self.engine.like(path, loc, kinds, body.get("within"), int(body.get("limit", 60)),
                                      date_range=body.get("date"), key=key)
        except ValueError as error:
            raise Refused(400, str(error)) from error
        except Unreadable as error:
            raise Refused(422, str(error)) from error
        kind = classify(path)
        kind = kind if isinstance(kind, Kind) else None
        reader = Store(default_file())
        try:
            indexed = reader.db.execute("SELECT 1 FROM files WHERE path = ? AND active = 1", (path,)).fetchone()
        finally:
            reader.close()
        return self._answer_json(answer) | {"like": {
            "path": path, "name": PurePath(path).name, "kind": kind.value if kind else None,
            "loc": loc, "place": place(kind, loc) if kind else None,
            "indexed": bool(indexed) and self.selection.decide(path) is not None}}

    def _same_file(self, copy: PurePath, digest: str) -> str | None:
        """The file of the index that `copy` is a copy of (the same size, then the same bytes), if any."""
        size_ = os.path.getsize(copy)
        reader = Store(default_file())
        try:
            candidates = [p for (p,) in reader.db.execute(
                "SELECT path FROM files WHERE size = ? AND active = 1 ORDER BY lower(path) LIKE ? DESC LIMIT 5",
                (size_, "%" + PurePath(copy).name.lower()))]
        finally:
            reader.close()
        for path in candidates:
            if self.selection.decide(path) is None or cloud_only(attributes(path)):
                continue
            try:
                with open(path, "rb") as handle:
                    if hashlib.file_digest(handle, "sha256").hexdigest() == digest:
                        return path
            except OSError:
                continue
        return None

    def _live(self, reader: Store):
        """Whether a file can be shown now (in the index, switched on, among what's searched), asked of the few paths
        history holds: listing every file instead took 0.2 s a keystroke with 10,000 of them."""
        known: dict[str, bool] = {}

        def live(path: str) -> bool:
            if path not in known:
                known[path] = self.selection.decide(path) is not None and reader.db.execute(
                    "SELECT 1 FROM files WHERE path = ? AND active = 1 AND state IN ('done', 'partial')",
                    (path,)).fetchone() is not None
            return known[path]
        return live

    def _went_back(self, path: str, query) -> None:
        """A file opened, shown or copied: remembered, with what was searched for it (unless history is off, or the
        file isn't one Dowse searches now: a switched-off folder's files leave no trace)."""
        if not self.settings.get("history", True) or self.selection.decide(path) is None:
            return
        reader = Store(default_file())
        try:
            active = reader.db.execute("SELECT 1 FROM files WHERE path = ? AND active = 1", (path,)).fetchone()
        finally:
            reader.close()
        if active:
            self.history.opened(path, query if isinstance(query, str) else None)

    def _file_hits(self, reader: Store, paths_: list[str]) -> list[dict]:
        """Files as results (no match: their opening words), for lists that aren't a search's."""
        out = []
        for path in dict.fromkeys(paths_):
            row = reader.db.execute(
                "SELECT f.path, f.kind, f.size, f.mtime, f.info, (SELECT s.id FROM segments s WHERE s.file = f.id "
                "AND s.kind IN ('chunk', 'page') ORDER BY s.id LIMIT 1) FROM files f WHERE f.path = ?", (path,)).fetchone()
            if row is None:
                continue
            path, kind, size_, mtime, info, segment = row
            kind = Kind(kind)
            head = (text_of(reader, segment) or "")[:320] if segment else ""
            out.append({"path": path, "name": PurePath(path).name, "folder": folder_text(path), "kind": kind.value,
                        "rank": len(out), "strong": True, "score": 0, "loc": None, "locEnd": None, "place": None,
                        "excerpt": " ".join(head.split()) or None, "mtime": mtime, "size": size_,
                        "info": json.loads(info or "{}"), "segment": segment or 0, "visual": kind in VISUAL,
                        "moments": []})
        return out

    def _indexed(self, path: str) -> tuple:
        """(path, kind, mtime) of a file in the index: pictures and media are only ever served for those."""
        reader = Store(default_file())
        try:
            row = reader.db.execute("SELECT path, kind, mtime FROM files WHERE path = ?", (path,)).fetchone()
        finally:
            reader.close()
        if row is None or self.selection.configured(path) is None:
            raise Refused(404, "not in the index")
        return row

    # The first run, and switching processor

    @staticmethod
    def _model(value) -> modelfiles.Model:
        if not isinstance(value, str):
            raise Refused(400, "Choose a supported search model")
        try:
            return modelfiles.get(value)
        except ValueError as error:
            raise Refused(400, str(error)) from error

    def _prepare_model(self, model: modelfiles.Model, backend: str, *, initial: bool = False,
                       processor: bool = False) -> None:
        """Prepare first; change the index only after downloads and real embedding validation succeed."""
        with self._model_lock:
            if self._closing:
                raise Refused(409, "Dowse is closing")
            if self._job and self._job.thread and self._job.thread.is_alive():
                raise Refused(409, "Another model or processor change is in progress")
            self._model_progress = {"target": model.id, "kind": "setup" if initial else "processor" if processor else "model",
                                    "previous": self.model.id,
                                    "stage": "model", "state": "running", "done": 0,
                                    "total": sum(f.size for f in modelfiles.missing(model=model)),
                                    "rate": 0, "left": None, "canCancel": True, "error": None}

            def progress(p: dict) -> None:
                self._model_progress = {**self._model_progress, **p,
                                        "state": "done" if p["stage"] == "done" else "running",
                                        "canCancel": p["stage"] not in ("activate", "done")}
                self.events.publish("modelChange", self._model_progress)
                if initial:
                    self._setup_progress = p
                    self.events.publish("setup", p)
                if processor:
                    self.events.publish("switching", p)

            def done(choice: runtime.Choice) -> None:
                self._activate_model(model, choice)
                event = "setupDone" if initial else "switched" if processor else "modelChanged"
                self.events.publish(event, {"choice": choice.describe(), "onGpu": choice.device is not None,
                                            "model": model.json()})

            def failed(why: str) -> None:
                cancelled = self._job.cancelled.is_set()
                if self.model.id == model.id and self._model_progress["previous"] != model.id:
                    # Activation committed; don't claim the old model is still selected after a late restart failure.
                    self._model_progress = {**self._model_progress, "state": "done", "stage": "done", "canCancel": False,
                                            "error": f"The model is active, but indexing needs a restart: {why}"}
                    self.events.publish("modelChange", self._model_progress)
                    self.events.publish("modelChanged", {"model": model.json()})
                    return
                self._model_progress = {**self._model_progress, "state": "cancelled" if cancelled else "failed",
                                        "stage": "cancelled" if cancelled else "failed", "canCancel": False,
                                        "error": None if cancelled else why}
                self.events.publish("modelChange", self._model_progress)
                if initial:
                    self.events.publish("setupFailed", {"why": "Preparation cancelled" if cancelled else why})
                if processor:
                    self.events.publish("switched", {"failed": why})

            self._job = setup.SetupJob(backend, progress, done, failed, model=model,
                                       choice=self.choice if not processor and self.choice else None, fallback=initial)
            self.events.publish("modelChange", self._model_progress)
            self._job.start()

    def _activate_model(self, model: modelfiles.Model, choice: runtime.Choice) -> None:
        with self._model_lock:
            if self._closing:
                raise InterruptedError("Dowse closed before model activation")
            store = Store()
            stopped = False
            committed = False
            try:
                runtime.save(choice)   # fail before modifying the index if configuration cannot be saved
                different = model.id != self.model.id or store.meta("vectors") not in (None, model.vector_tag)
                if different:
                    stopped = True
                    self.indexing.quiesce()
                    self.engine.stop(wait=True)
                    with writer_lock(store):
                        store.change_model(model)
                    committed = True
                elif store.meta("vectors") is None and not self.indexing.started:
                    with writer_lock(store):
                        with store.transaction():
                            store.set_meta("vectors", model.vector_tag)
                            store.set_meta("search_model", model.id)
                # The committed DB identity is authoritative even if settings cannot be saved (e.g. disk full).
                self.model = model
                self.settings["search_model"] = model.id
                with contextlib.suppress(OSError):
                    settings.save(self.settings)
                old = self.engine
                self.choice = choice
                self.engine = self._engine(choice)
                old.stop()
                if stopped:
                    self.indexing.config = self._config()
                self.begin()
                self._index_changed()
            except Exception:
                if stopped:
                    if committed:
                        self.model = model   # no old-model query may run after the new index identity committed
                    self.engine = self._engine(self.choice)
                    self.indexing.config = self._config()
                    self.indexing.resume()
                raise
            finally:
                store.close()

    def _start_setup(self, backend: str) -> None:
        self._prepare_model(self.model, backend, initial=True)

    def change_settings(self, body: dict) -> dict:
        with self._model_lock:
            if self._job and self._job.thread and self._job.thread.is_alive() and any(
                    k in body for k in ("backend", "rebuild", "encrypt")):
                raise Refused(409, "Wait for the model preparation to finish before changing the processor or index")
            return self._change_settings(body)

    def _change_settings(self, body: dict) -> dict:
        if "theme" in body:
            mode = body["theme"]
            if mode not in ("system", "light", "dark"):
                raise Refused(400, "Choose Follow Windows, Light or Dark")
            with self._theme_lock:
                saved = {**self.settings, "theme": mode}
                settings.save(saved)   # a failed save must not change the live theme
                self.settings = saved
                self._update_theme()
        if "hotkey" in body:
            if self.native and not self.native.set_hotkey(body["hotkey"]):
                return {"ok": False, "why": "Another app uses that one"}
            self._save(hotkey=body["hotkey"])
        if "startup" in body:
            winapi.set_starts_with_windows(bool(body["startup"]))
        if "pluggedInOnly" in body:
            self._save(plugged_in_only=bool(body["pluggedInOnly"]))
            self.indexing.configure(self._config())
        if "paused" in body:
            self.indexing.set_paused(bool(body["paused"]))
            if self.native:
                self.native.paused(bool(body["paused"]))
        if "screenshots" in body:   # new screenshots into Dowsed files, from now on
            self._save(screenshots=bool(body["screenshots"]))
            self._screenshot_switch()
        if "history" in body:   # off: what was kept goes too
            self._save(history=bool(body["history"]))
            if not body["history"]:
                self.history.forget()
        if "explorerMenu" in body:
            self._save(explorer_menu=bool(body["explorerMenu"]))
            threading.Thread(target=shell.register if body["explorerMenu"] else shell.unregister, daemon=True).start()
        if "skipKinds" in body or "skipExtensions" in body:
            current = Skip.from_settings(self.settings)
            skip = Skip.of(body.get("skipKinds", [k.value for k in current.kinds]),
                           body.get("skipExtensions", list(current.extensions)))
            self._save(**skip.to_settings())
            self.indexing.configure(self._config())
        if "folders" in body:
            folders = tuple(body["folders"])
            watching = Selection(folders)
            self.choose(Selection(folders, self.selection.files,
                                  tuple(r for r in self.selection.removed if watching.watched(r)),
                                  self.selection.inactive, self.selection.forgotten))
        if "removed" in body:
            self.choose(Selection(self.selection.folders, self.selection.files, tuple(body["removed"]),
                                  self.selection.inactive, self.selection.forgotten))
        if "backend" in body:
            self._switch(body["backend"])
        if body.get("rebuild"):
            self.indexing.rebuild(done=self.engine.reload)
        if "encrypt" in body:
            self._save(encrypt_index=bool(body["encrypt"]))
            log.info("index encryption %s: restarting", "on" if body["encrypt"] else "off")
            winapi.start_again()
            self.native and self.native.quit()
        return {"ok": True, "state": self.state()}

    def _switch(self, backend: str) -> None:
        if backend not in ("auto", *runtime.BACKENDS):
            raise Refused(400, "Unknown processor backend")
        self._prepare_model(self.model, backend, processor=True)
