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


@pytest.fixture(autouse=True)
def _never_this_pcs_windows_integration(monkeypatch):
    """Explorer's menu and start-with-Windows are this Windows account's, shared with the Dowse in use: an app a test
    starts (announced, as the real one is) must not re-point them at the test's Python. Tests of the menu itself
    register it under a scratch key of their own (`shell.register(classes, …)`)."""
    from dowse.desktop import shell, winapi

    monkeypatch.setattr(shell, "keep_registered", lambda: None)
    monkeypatch.setattr(winapi, "starts_with_windows", lambda: False)
    monkeypatch.setattr(winapi, "set_starts_with_windows", lambda on: None)
