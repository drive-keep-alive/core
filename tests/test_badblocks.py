"""badblocks scan: summary parsing, skip logic, DB writes."""

import subprocess

from sqlmodel import select

import poll_handling
from database_handling import session_scope
from models import BadBlockScan, Drive

OK_OUTPUT = "Pass completed, 3 bad blocks found. (3/0/0 errors)\n"


def _seed_drive(session, device="/dev/sdb"):
    d = Drive(device=device)
    session.add(d)
    session.flush()
    return d.id


def test_run_badblocks_completed(db, monkeypatch):
    with session_scope() as s:
        did = _seed_drive(s)
    proc = subprocess.CompletedProcess(["badblocks", "-s", "-v", "/dev/sdb"], 0,
                                       stdout=OK_OUTPUT, stderr="")
    monkeypatch.setattr(poll_handling, "_run_cmd", lambda *a: proc)
    poll_handling._run_badblocks({"id": did, "device": "/dev/sdb"})
    with session_scope() as s:
        scan = s.exec(select(BadBlockScan)).one()
        assert scan.status == "COMPLETED"
        assert scan.bad_blocks == 3
        assert scan.error is None
        assert scan.finished_at is not None


def test_run_badblocks_reads_summary_from_stderr(db, monkeypatch):
    # badblocks -v writes progress and the summary to stderr
    with session_scope() as s:
        did = _seed_drive(s)
    proc = subprocess.CompletedProcess([], 0, stdout="",
                                       stderr="Pass completed, 5 bad blocks found. (5/0/0 errors)\n")
    monkeypatch.setattr(poll_handling, "_run_cmd", lambda *a: proc)
    poll_handling._run_badblocks({"id": did, "device": "/dev/sdb"})
    with session_scope() as s:
        scan = s.exec(select(BadBlockScan)).one()
        assert scan.status == "COMPLETED"
        assert scan.bad_blocks == 5


def test_run_badblocks_no_summary_is_failed(db, monkeypatch):
    with session_scope() as s:
        did = _seed_drive(s)
    proc = subprocess.CompletedProcess([], 0, stdout="nothing useful here", stderr="")
    monkeypatch.setattr(poll_handling, "_run_cmd", lambda *a: proc)
    poll_handling._run_badblocks({"id": did, "device": "/dev/sdb"})
    with session_scope() as s:
        scan = s.exec(select(BadBlockScan)).one()
        assert scan.status == "FAILED"
        assert scan.bad_blocks is None
        assert scan.error is not None


def test_run_badblocks_missing_binary(db, monkeypatch):
    with session_scope() as s:
        did = _seed_drive(s)
    monkeypatch.setattr(poll_handling, "_run_cmd", lambda *a: None)
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

    def fake_run(*args):
        called.append(args)
        return subprocess.CompletedProcess(list(args), 0, stdout=OK_OUTPUT, stderr="")

    monkeypatch.setattr(poll_handling, "_run_cmd", fake_run)
    poll_handling._run_badblocks({"id": did, "device": "/dev/sdb"})
    assert called == []
    with session_scope() as s:
        scans = s.exec(select(BadBlockScan)).all()
        assert len(scans) == 1
        assert scans[0].status == "RUNNING"
