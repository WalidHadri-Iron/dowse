import os

import pytest

from dowse import modelfiles, runtime


@pytest.fixture(scope="session")
def choice() -> runtime.Choice:
    """The runtime the server tests use: $DOWSE_BACKEND (auto, cpu, vulkan, cuda), else what setup chose."""
    if modelfiles.missing(model=modelfiles.DEFAULT):
        pytest.skip("model files aren't downloaded (dowse setup)")
    wanted = os.environ.get("DOWSE_BACKEND")
    chosen = runtime.choose(wanted) if wanted else runtime.load()
    if chosen is None or not runtime.installed(chosen.backend):
        pytest.skip("no llama.cpp runtime installed (dowse setup)")
    return chosen


@pytest.fixture(scope="session")
def server(choice):
    with runtime.Server(choice, model=modelfiles.DEFAULT) as running:
        yield running


@pytest.fixture(autouse=True)
def _reading_never_waits_for_the_tests(monkeypatch):
    """The person-first wait (`foreground`) is about someone using the app: in tests, requests don't hold reading."""
    from dowse import foreground

    monkeypatch.setattr(foreground, "QUIET", 0.0)
