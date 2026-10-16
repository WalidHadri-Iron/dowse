"""The whole Explore map carries search's calendar date, without changing its cached positions."""

import json
from datetime import datetime

from dowse.store import Store
from test_desktop import app, request


def test_map_dates_use_capture_time_then_mtime_and_agree_with_browse(app):
    running, _, _ = app
    store = Store()
    paths = list(store.db.execute("SELECT path, kind FROM files ORDER BY id"))
    modified = datetime(2026, 7, 13, 23, 59, 59).timestamp()
    for path, kind in paths:
        info = {"taken": "2025:07:12 12:30:00"} if kind == "image" else {"taken": "invalid"}
        store.db.execute("UPDATE files SET mtime=?,info=? WHERE path=?", (modified, json.dumps(info), path))
    store.db.commit()
    store.close()
    running._map = None
    response = request(running.server, "GET", "/api/explore")
    assert response[0] == 200, response[2]
    floor = json.loads(response[2])
    assert len(floor["pieces"]) == 4
    for piece in floor["pieces"]:
        assert piece["fileDate"] == ("2025-07-12" if piece["kind"] == "image" else "2026-07-13")
    for day in ("2025-07-12", "2026-07-13", "2024-01-01"):
        filtered = json.loads(request(running.server, "GET", f"/api/browse?from={day}&to={day}")[2])
        assert {h["path"] for h in filtered["hits"]} == {p["path"] for p in floor["pieces"] if p["fileDate"] == day}
    assert json.loads(request(running.server, "GET", "/api/explore")[2]) == floor
