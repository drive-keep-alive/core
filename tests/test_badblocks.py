"""badblocks scan: streaming tail, summary parsing, skip logic, DB writes."""

import io
import subprocess

from sqlmodel import select

import poll_handling
from database_handling import session_scope
from models import BadBlockScan, Drive

OK_OUTPUT = "Pass completed, 3 bad blocks found. (3/0/0 errors)\n"


class FakeProc:
    """Popen stand-in exposing a byte stream and wait()."""

    def __init__(self, output, returncode=0):
        self.stdout = io.BytesIO(output.encode("utf-8"))
        self.returncode = returncode
        self._waited = False

    def wait(self):
        self._waited = True
        return self.returncode


def _seed_drive(session, device="/dev/sdb"):
    d = Drive(device=device)
    session.add(d)
    session.flush()
    return d.id


def _patch_proc(monkeypatch, proc):
    monkeypatch.setattr(poll_handling, "_badblocks_proc", lambda node: proc)


# ---- _badblocks_proc --------------------------------------------------------


def test_badblocks_proc_command_and_merge(monkeypatch):
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(poll_handling.subprocess, "Popen", fake_popen)
    poll_handling._badblocks_proc("/dev/sdb")
    assert captured["argv"] == ["badblocks", "-s", "-v", "/dev/sdb"]
    assert captured["kwargs"] == {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}


def test_badblocks_proc_missing_binary(monkeypatch):
    def boom(argv, **kwargs):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(poll_handling.subprocess, "Popen", boom)
    assert poll_handling._badblocks_proc("/dev/sdb") is None


# ---- _drain_tail ------------------------------------------------------------


def test_drain_tail_bounded_memory():
    # a real -s -v scan emits a long progress stream; the drain must not hold
    # it all, but must keep the trailing summary
    bulk = "Testing blocks 0 to 2000000" + (" 100\b" * 400_000)
    proc = FakeProc(bulk + OK_OUTPUT)
    tail = poll_handling._drain_tail(proc)
    assert len(tail) <= 8192
    assert "3 bad blocks found" in tail
    assert proc._waited is False  # wait() is the caller's job


# ---- _run_badblocks ---------------------------------------------------------


def test_run_badblocks_completed(db, monkeypatch):
    with session_scope() as s:
        did = _seed_drive(s)
    _patch_proc(monkeypatch, FakeProc(OK_OUTPUT))
    poll_handling._run_badblocks({"id": did, "device": "/dev/sdb"})
    with session_scope() as s:
        scan = s.exec(select(BadBlockScan)).one()
        assert scan.status == "COMPLETED"
        assert scan.bad_blocks == 3
        assert scan.error is None
        assert scan.finished_at is not None


def test_run_badblocks_reads_summary_from_stderr(db, monkeypatch):
    # some badblocks versions print the summary to stderr; _badblocks_proc
    # merges it into stdout, so the scan sees it on the one stream
    with session_scope() as s:
        did = _seed_drive(s)
    _patch_proc(monkeypatch, FakeProc("Pass completed, 5 bad blocks found. (5/0/0 errors)\n"))
    poll_handling._run_badblocks({"id": did, "device": "/dev/sdb"})
    with session_scope() as s:
        scan = s.exec(select(BadBlockScan)).one()
        assert scan.status == "COMPLETED"
        assert scan.bad_blocks == 5


def test_run_badblocks_no_summary_is_failed(db, monkeypatch):
    with session_scope() as s:
        did = _seed_drive(s)
    _patch_proc(monkeypatch, FakeProc("nothing useful here"))
    poll_handling._run_badblocks({"id": did, "device": "/dev/sdb"})
    with session_scope() as s:
        scan = s.exec(select(BadBlockScan)).one()
        assert scan.status == "FAILED"
        assert scan.bad_blocks is None
        assert scan.error is not None


def test_run_badblocks_missing_binary(db, monkeypatch):
    with session_scope() as s:
        did = _seed_drive(s)
    monkeypatch.setattr(poll_handling, "_badblocks_proc", lambda node: None)
    poll_handling._run_badblocks({"id": did, "device": "/dev/sdb"})
    with session_scope() as s:
        scan = s.exec(select(BadBlockScan)).one()
        assert scan.status == "FAILED"
        assert scan.error == "badblocks not found"


def test_run_badblocks_skips_when_scan_running(db, monkeypatch):
    with session_scope() as s:
        did = _seed_drive(s)
        s.add(BadBlockScan(drive_id=did, status="RUNNING"))
    called = []
    monkeypatch.setattr(poll_handling, "_badblocks_proc",
                        lambda node: called.append(node) or None)
    poll_handling._run_badblocks({"id": did, "device": "/dev/sdb"})
    assert called == []
    with session_scope() as s:
        scans = s.exec(select(BadBlockScan)).all()
        assert len(scans) == 1
        assert scans[0].status == "RUNNING"
