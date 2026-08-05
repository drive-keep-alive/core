"""SMART parsing: canonical attrs, ATA/NVMe paths, selftest log handling."""

import subprocess
from datetime import timedelta

from sqlmodel import select

import poll_handling
from database_handling import session_scope
from models import Drive, SelfTestResult, SmartAttribute, utcnow


class FakeAttr:
    def __init__(self, name, current=None, worst=None, threshold=None, raw=None, flags=None):
        self.name = name
        self.current = current
        self.worst = worst
        self.threshold = threshold
        self.raw = raw
        self.flags = flags


def make_device(attributes, if_attributes=None, assessment=None):
    """pySMART Device stand-in returning fixed fake data."""
    class FakeDevice:
        def __init__(self, node):
            self.node = node
            self.attributes = attributes
            self.if_attributes = if_attributes
            self.assessment = assessment
    return FakeDevice


class FakeNvmeAttrs:
    criticalWarning = 0
    temperature = 41
    availableSpare = 100
    percentageUsed = 3
    powerOnHours = 50
    unsafeShutdowns = 1
    integrityErrors = 0
    powerCycles = 4


# ---- _canonical_attr --------------------------------------------------------


def test_canonical_attr_aliases():
    assert poll_handling._canonical_attr("Temperature_Celsius") == "Temperature_Celsius"
    assert poll_handling._canonical_attr("temperature") == "Temperature_Celsius"
    assert poll_handling._canonical_attr("Composite Temperature") == "Temperature_Celsius"
    assert poll_handling._canonical_attr("Reallocated Sector Ct") == "Reallocated_Sector_Ct"
    assert poll_handling._canonical_attr("Media and Data Integrity Errors") == "Media_Errors"
    assert poll_handling._canonical_attr("Reported Uncorrectable") == "Offline_Uncorrectable"


def test_canonical_attr_unknown_lowercases():
    assert poll_handling._canonical_attr("Some Odd Name") == "some_odd_name"


# ---- _coerce_int ------------------------------------------------------------


def test_coerce_int():
    assert poll_handling._coerce_int(None) is None
    assert poll_handling._coerce_int("12") == 12
    assert poll_handling._coerce_int(12) == 12
    assert poll_handling._coerce_int("abc") is None


# ---- _smart_attrs: ATA path -------------------------------------------------


def test_smart_attrs_ata(monkeypatch):
    attrs = [
        None,  # pySMART pads the list; must be skipped
        FakeAttr("Temperature_Celsius", current=40, raw=40),
        FakeAttr("Reallocated Sector Ct", current=100, worst=100, threshold=10, raw=0, flags="P--"),
        FakeAttr("Some Odd Name", raw=7),
    ]
    monkeypatch.setattr(poll_handling, "Device", make_device(attrs, assessment="PASS"))
    rows = poll_handling._smart_attrs("/dev/sda")
    assert rows is not None
    by_name = {r.name: r for r in rows}
    assert by_name["Temperature_Celsius"].raw == 40
    assert by_name["Reallocated_Sector_Ct"].normalized == 100
    assert by_name["Reallocated_Sector_Ct"].threshold == 10
    assert by_name["Reallocated_Sector_Ct"].flags == "P--"
    assert by_name["some_odd_name"].raw == 7
    assert by_name["Overall_Health"].raw == 0  # PASS


def test_smart_attrs_assessment_fail(monkeypatch):
    monkeypatch.setattr(poll_handling, "Device", make_device([], assessment="FAIL"))
    rows = poll_handling._smart_attrs("/dev/sda")
    assert len(rows) == 1
    assert rows[0].name == "Overall_Health"
    assert rows[0].raw == 1


def test_smart_attrs_no_data_returns_none(monkeypatch):
    monkeypatch.setattr(poll_handling, "Device", make_device([], assessment=None))
    assert poll_handling._smart_attrs("/dev/sda") is None


def test_smart_attrs_device_raises_returns_none(monkeypatch):
    def boom(node):
        raise OSError("no such device")
    monkeypatch.setattr(poll_handling, "Device", boom)
    assert poll_handling._smart_attrs("/dev/sda") is None


# ---- _smart_attrs: NVMe path ------------------------------------------------


def test_smart_attrs_nvme_uses_if_attributes(monkeypatch):
    monkeypatch.setattr(
        poll_handling, "Device",
        make_device([None] * 256, if_attributes=FakeNvmeAttrs(), assessment="PASS"),
    )
    rows = poll_handling._smart_attrs("/dev/nvme0n1")
    by_name = {r.name: r for r in rows}
    assert by_name["Critical_Warning"].raw == 0
    assert by_name["Temperature_Celsius"].raw == 41
    assert by_name["Available_Spare"].raw == 100
    assert by_name["Percentage_Used"].raw == 3
    assert by_name["Power_On_Hours"].raw == 50
    assert by_name["Unsafe_Shutdowns"].raw == 1
    assert by_name["Media_Errors"].raw == 0
    assert by_name["Power_Cycles"].raw == 4


def test_smart_attrs_nvme_without_if_attributes(monkeypatch):
    monkeypatch.setattr(
        poll_handling, "Device", make_device([None] * 256, if_attributes=None, assessment=None),
    )
    assert poll_handling._smart_attrs("/dev/nvme0n1") is None


# ---- selftest log -----------------------------------------------------------


def test_map_selftest_status_dict_and_bare():
    assert poll_handling._map_selftest_status({"string": "Completed without error", "passed": True, "value": 0}) == "PASSED"
    assert poll_handling._map_selftest_status({"string": "Completed with errors"}) == "FAILED"
    assert poll_handling._map_selftest_status({"string": "Self-test routine in progress..."}) == "IN_PROGRESS"
    assert poll_handling._map_selftest_status("Completed without error") == "PASSED"
    assert poll_handling._map_selftest_status("In progress") == "IN_PROGRESS"
    assert poll_handling._map_selftest_status("Aborted by host") == "FAILED"
    assert poll_handling._map_selftest_status(None) == "FAILED"


def test_selftest_json_parses_exit_128(monkeypatch):
    # bit 7 set = log contains error records; must still parse the JSON
    proc = subprocess.CompletedProcess(["smartctl", "-l", "selftest", "-j", "/dev/sda"], 128,
                                       stdout='{"json_log": {"selftest": {"table": []}}}', stderr="")
    monkeypatch.setattr(poll_handling, "_run_cmd", lambda *a: proc)
    data = poll_handling._selftest_json("/dev/sda")
    assert data == {"json_log": {"selftest": {"table": []}}}


def test_selftest_json_missing_binary(monkeypatch):
    monkeypatch.setattr(poll_handling, "_run_cmd", lambda *a: None)
    assert poll_handling._selftest_json("/dev/sda") is None


def test_selftest_json_invalid_output(monkeypatch):
    proc = subprocess.CompletedProcess([], 0, stdout="not json", stderr="")
    monkeypatch.setattr(poll_handling, "_run_cmd", lambda *a: proc)
    assert poll_handling._selftest_json("/dev/sda") is None


def test_sync_selftest_status_marks_passed(db, monkeypatch):
    with session_scope() as s:
        d = Drive(device="/dev/sda")
        s.add(d)
        s.flush()
        did = d.id
        s.add(SelfTestResult(drive_id=did, test_type="short", status="QUEUED"))
    data = {"json_log": {"selftest": {"table": [{"status": {"value": 0, "passed": True, "string": "Completed without error"}}]}}}
    monkeypatch.setattr(poll_handling, "_selftest_json", lambda node: data)
    poll_handling._sync_selftest_status({"id": did, "device": "/dev/sda"})
    with session_scope() as s:
        row = s.exec(select(SelfTestResult)).one()
        assert row.status == "PASSED"
        assert row.finished_at is not None


def test_sync_selftest_status_keeps_in_progress(db, monkeypatch):
    with session_scope() as s:
        d = Drive(device="/dev/sda")
        s.add(d)
        s.flush()
        did = d.id
        s.add(SelfTestResult(drive_id=did, test_type="long", status="IN_PROGRESS"))
    data = {"json_log": {"selftest": {"table": [{"status": {"string": "Self-test routine in progress"}}]}}}
    monkeypatch.setattr(poll_handling, "_selftest_json", lambda node: data)
    poll_handling._sync_selftest_status({"id": did, "device": "/dev/sda"})
    with session_scope() as s:
        row = s.exec(select(SelfTestResult)).one()
        assert row.status == "IN_PROGRESS"
        assert row.finished_at is None


def test_sync_selftest_status_ignores_when_none_pending(db, monkeypatch):
    with session_scope() as s:
        d = Drive(device="/dev/sda")
        s.add(d)
        s.flush()
        did = d.id
        s.add(SelfTestResult(drive_id=did, test_type="short", status="PASSED"))
    data = {"json_log": {"selftest": {"table": [{"status": {"string": "Completed with errors"}}]}}}
    monkeypatch.setattr(poll_handling, "_selftest_json", lambda node: data)
    poll_handling._sync_selftest_status({"id": did, "device": "/dev/sda"})
    with session_scope() as s:
        row = s.exec(select(SelfTestResult)).one()
        assert row.status == "PASSED"


def test_sync_selftest_status_no_data_is_noop(db, monkeypatch):
    with session_scope() as s:
        d = Drive(device="/dev/sda")
        s.add(d)
        s.flush()
        did = d.id
        s.add(SelfTestResult(drive_id=did, test_type="short", status="QUEUED"))
    monkeypatch.setattr(poll_handling, "_selftest_json", lambda node: None)
    poll_handling._sync_selftest_status({"id": did, "device": "/dev/sda"})
    with session_scope() as s:
        row = s.exec(select(SelfTestResult)).one()
        assert row.status == "QUEUED"


# ---- trigger + poll ---------------------------------------------------------


def test_trigger_test_skips_when_active(db, monkeypatch):
    with session_scope() as s:
        d = Drive(device="/dev/sda")
        s.add(d)
        s.flush()
        did = d.id
        s.add(SelfTestResult(drive_id=did, test_type="short", status="QUEUED"))
    called = []
    monkeypatch.setattr(poll_handling, "_run_cmd",
                        lambda *a: called.append(list(a)) or subprocess.CompletedProcess(list(a), 0, stdout="", stderr=""))
    poll_handling._trigger_test({"id": did, "device": "/dev/sda"}, "short")
    assert called == []


def test_trigger_test_adds_queued(db, monkeypatch):
    with session_scope() as s:
        d = Drive(device="/dev/sda")
        s.add(d)
        s.flush()
        did = d.id
    called = []
    monkeypatch.setattr(poll_handling, "_run_cmd",
                        lambda *a: called.append(list(a)) or subprocess.CompletedProcess(list(a), 0, stdout="", stderr=""))
    poll_handling._trigger_test({"id": did, "device": "/dev/sda"}, "short")
    assert called == [["smartctl", "-t", "short", "/dev/sda"]]
    with session_scope() as s:
        row = s.exec(select(SelfTestResult)).one()
        assert row.test_type == "short"
        assert row.status == "QUEUED"


def test_trigger_test_failed_command_adds_nothing(db, monkeypatch):
    with session_scope() as s:
        d = Drive(device="/dev/sda")
        s.add(d)
        s.flush()
        did = d.id
    proc = subprocess.CompletedProcess([], 1, stdout="", stderr="command not supported")
    monkeypatch.setattr(poll_handling, "_run_cmd", lambda *a: proc)
    poll_handling._trigger_test({"id": did, "device": "/dev/sda"}, "short")
    with session_scope() as s:
        assert s.exec(select(SelfTestResult)).all() == []


def test_poll_smart_node_persists(db, monkeypatch):
    with session_scope() as s:
        d = Drive(device="/dev/sda")
        s.add(d)
        s.flush()
        did = d.id
    monkeypatch.setattr(poll_handling, "Device",
                        make_device([FakeAttr("Temperature_Celsius", raw=40)], assessment="PASS"))
    monkeypatch.setattr(poll_handling, "_selftest_json", lambda node: None)
    poll_handling._poll_smart_node({"id": did, "device": "/dev/sda"})
    with session_scope() as s:
        rows = s.exec(select(SmartAttribute).where(SmartAttribute.drive_id == did)).all()
        names = {r.name for r in rows}
        assert "Temperature_Celsius" in names
        assert "Overall_Health" in names
        assert all(r.timestamp is not None for r in rows)


def test_latest_attrs_by_name_keeps_newest(db):
    with session_scope() as s:
        d = Drive(device="/dev/sda")
        s.add(d)
        s.flush()
        did = d.id
        t1 = utcnow() - timedelta(hours=2)
        t2 = utcnow()
        s.add(SmartAttribute(drive_id=did, name="Temperature_Celsius", raw=40, timestamp=t1))
        s.add(SmartAttribute(drive_id=did, name="temperature", raw=42, timestamp=t2))
        s.add(SmartAttribute(drive_id=did, name="Reallocated_Sector_Ct", raw=3, timestamp=t2))
    with session_scope() as s:
        latest = poll_handling._latest_attrs_by_name(did, s)
    assert latest["Temperature_Celsius"] == 42  # newest wins, alias collapsed
    assert latest["Reallocated_Sector_Ct"] == 3


def test_latest_attrs_by_name_large_history(db):
    # 2000 snapshots; the max(id) subquery must still return only the newest
    # row per attribute without loading the whole history
    with session_scope() as s:
        d = Drive(device="/dev/sda")
        s.add(d)
        s.flush()
        did = d.id
        base = utcnow() - timedelta(days=30)
        for i in range(1000):
            ts = base + timedelta(minutes=i)
            s.add(SmartAttribute(drive_id=did, name="Temperature_Celsius",
                                 raw=30 + i % 40, timestamp=ts))
            s.add(SmartAttribute(drive_id=did, name="Reallocated_Sector_Ct",
                                 raw=i % 5, timestamp=ts))
    with session_scope() as s:
        latest = poll_handling._latest_attrs_by_name(did, s)
    assert latest["Temperature_Celsius"] == 69   # 30 + 999 % 40
    assert latest["Reallocated_Sector_Ct"] == 4  # 999 % 5


def test_prune_smart_attributes_drops_old_rows(db, config_dict):
    import asyncio
    config_dict["database"]["retention_days"] = 1
    with session_scope() as s:
        d = Drive(device="/dev/sda")
        s.add(d)
        s.flush()
        did = d.id
        old = utcnow() - timedelta(days=3)
        fresh = utcnow() - timedelta(hours=6)
        s.add(SmartAttribute(drive_id=did, name="Temperature_Celsius", raw=40, timestamp=old))
        s.add(SmartAttribute(drive_id=did, name="Temperature_Celsius", raw=41, timestamp=fresh))
    asyncio.run(poll_handling.prune_smart_attributes())
    with session_scope() as s:
        rows = s.exec(select(SmartAttribute)).all()
        assert len(rows) == 1
        assert rows[0].raw == 41
