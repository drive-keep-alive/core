"""discover_drives TTL cache: skips the pyudev walk + DB commit on hot paths."""

import time

import poll_handling


def _mock_scan(monkeypatch, calls):
    monkeypatch.setattr(poll_handling, "_discover_cache", None)
    monkeypatch.setattr(
        poll_handling, "_discover_drives",
        lambda: calls.append(1) or [{"id": 1, "device": "/dev/sda"}],
    )


def test_discover_drives_cached(monkeypatch):
    calls = []
    _mock_scan(monkeypatch, calls)
    first = poll_handling.discover_drives()
    second = poll_handling.discover_drives()
    assert first is second  # same list handed out, no re-scan
    assert len(calls) == 1


def test_discover_drives_expires(monkeypatch):
    calls = []
    _mock_scan(monkeypatch, calls)
    poll_handling.discover_drives()
    stale = (time.monotonic() - 1000, [{"id": 99, "device": "/dev/sdx"}])
    monkeypatch.setattr(poll_handling, "_discover_cache", stale)
    fresh = poll_handling.discover_drives()
    assert fresh == [{"id": 1, "device": "/dev/sda"}]
    assert len(calls) == 2
