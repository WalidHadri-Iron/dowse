"""Device changes keep canonical terminal job state, even after the legacy completion event."""

import json
from types import SimpleNamespace

import pytest

from dowse import modelfiles, runtime
from dowse.desktop import setup
from dowse.store import Store
from test_desktop import app, request, controlled_model_job


def test_repeated_processor_switches_finish_without_rebuilding_vectors(app, monkeypatch):
    running, _, _ = app
    monkeypatch.setattr(running, "begin", lambda: None)
    monkeypatch.setattr(modelfiles, "missing", lambda **kwargs: [])
    monkeypatch.setattr(runtime, "load", lambda: runtime.Choice("cpu", None))
    monkeypatch.setattr(runtime, "installed", lambda backend: True)
    monkeypatch.setattr(setup.SetupJob, "_validate", lambda self, choice: None)
    monkeypatch.setattr(setup.speed, "load", lambda *args, **kwargs: SimpleNamespace(measured=True))
    events = []
    publish = running.events.publish
    monkeypatch.setattr(running.events, "publish", lambda name, data: (events.append((name, dict(data))), publish(name, data)))
    store = Store()
    before = store.vector_count(), store.known()
    try:
        for backend in ("cpu", "auto", "cpu"):
            choice = runtime.Choice("cpu", None) if backend == "cpu" else runtime.Choice("vulkan", "Vulkan1")
            monkeypatch.setattr(runtime, "choose", lambda *args, **kwargs: choice)
            events.clear()
            status, _, body = request(running.server, "POST", "/api/settings", {"backend": backend})
            assert status == 200, body
            running._job.thread.join(5)
            assert not running._job.thread.is_alive()
            assert running.choice == choice
            job = json.loads(request(running.server, "GET", "/api/models")[2])["job"]
            assert (job["kind"], job["state"], job["stage"], job["total"]) == ("processor", "done", "done", 0)
            names = [(name, data.get("stage")) for name, data in events]
            assert names.index(("switched", None)) < names.index(("switching", "done"))
            assert [data for name, data in events if name == "modelChange"][-1]["state"] == "done"
            assert (store.vector_count(), store.known()) == before
    finally:
        store.close()


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_failed_or_cancelled_processor_change_keeps_previous_device_and_vectors(app, monkeypatch, outcome):
    running, _, _ = app
    before_choice = running.choice
    release = controlled_model_job(monkeypatch, running, "failed")
    store = Store()
    before = store.vector_count(), store.known()
    try:
        assert request(running.server, "POST", "/api/settings", {"backend": "cpu"})[0] == 200
        if outcome == "cancelled":
            assert request(running.server, "POST", "/api/models/cancel", {})[0] == 200
        else:
            release.set()
        running._job.thread.join(5)
        assert running._model_progress["kind"] == "processor"
        assert running._model_progress["state"] == outcome
        assert running.choice == before_choice
        assert (store.vector_count(), store.known()) == before
    finally:
        release.set()
        store.close()
