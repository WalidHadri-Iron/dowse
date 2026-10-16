"""The person first: reading waits while the interface is in use, goes on soon after, and never waits too long."""

import time

from dowse import foreground


def test_reading_waits_while_the_interface_is_used_and_not_forever(monkeypatch):
    monkeypatch.setattr(foreground, "QUIET", 0.3)
    monkeypatch.setattr(foreground, "MOST", 1.0)
    monkeypatch.setattr(foreground, "_last", 0.0)
    assert foreground.wait() < 0.05          # nobody here: no wait
    foreground.touch()
    assert 0.25 < foreground.wait() < 0.6     # until it's been quiet a moment
    used_until = time.monotonic() + 3.0       # used without a pause: the wait still ends
    monkeypatch.setattr(foreground, "busy", lambda: time.monotonic() < used_until)
    assert 0.9 < foreground.wait() < 1.3      # at most MOST
