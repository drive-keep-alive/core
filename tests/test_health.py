"""Health labels and dashboard status from SMART attribute data."""

from types import SimpleNamespace

import psutil
from sqlmodel import select

import poll_handling
from database_handling import session_scope
from models import Drive, SelfTestResult, SmartAttribute


# ---- _drive_health_label ----------------------------------------------------


def test_health_label_ok_when_empty(config_dict):
    assert poll_handling._drive_health_label({}) == "ok"
    assert poll_handling._drive_health_label({"Available_Spare": None}) == "ok"


def test_health_label_ata_warn(config_dict):
    assert poll_handling._drive_health_label({"Reallocated_Sector_Ct": 1}) == "warn"
    assert poll_handling._drive_health_label({"Current_Pending_Sector": 1}) == "warn"
    assert poll_handling._drive_health_label({"Offline_Uncorrectable": 1}) == "warn"
    assert poll_handling._drive_health_label({"Spin_Retry_Count": 1}) == "warn"
    assert poll_handling._drive_health_label({"Temperature_Celsius": 50}) == "warn"


def test_health_label_ata_critical(config_dict):
    assert poll_handling._drive_health_label({"Reallocated_Sector_Ct": 10}) == "critical"
    assert poll_handling._drive_health_label({"Offline_Uncorrectable": 5}) == "critical"
    assert poll_handling._drive_health_label({"Temperature_Celsius": 60}) == "critical"


def test_health_label_nvme_warn(config_dict):
    assert poll_handling._drive_health_label({"Percentage_Used": 80}) == "warn"
    assert poll_handling._drive_health_label({"Media_Errors": 1}) == "warn"
    assert poll_handling._drive_health_label({"Unsafe_Shutdowns": 10}) == "warn"
    assert poll_handling._drive_health_label({"Available_Spare": 10}) == "warn"


def test_health_label_nvme_critical(config_dict):
    assert poll_handling._drive_health_label({"Percentage_Used": 100}) == "critical"
    assert poll_handling._drive_health_label({"Media_Errors": 5}) == "critical"
    assert poll_handling._drive_health_label({"Available_Spare": 1}) == "critical"


def test_health_label_overall_and_critical_warning(config_dict):
    assert poll_handling._drive_health_label({"Overall_Health": 1}) == "critical"
    assert poll_handling._drive_health_label({"Critical_Warning": 1}) == "critical"


def test_health_label_critical_beats_warn(config_dict):
    attrs = {"Reallocated_Sector_Ct": 1, "Percentage_Used": 100}
    assert poll_handling._drive_health_label(attrs) == "critical"


# ---- get_dashboard_status ---------------------------------------------------


def _seed_drive(session, device="/dev/sda", model="Fake Drive"):
    d = Drive(device=device, model=model, capacity_bytes=1_000_000, mount_point="/mnt/data")
    session.add(d)
    session.flush()
    return d.id


def test_dashboard_status_shape(db, config_dict):
    with session_scope() as s:
        did = _seed_drive(s)
    out = poll_handling.get_dashboard_status()
    assert len(out) == 1
    row = out[0]
    assert row["device"] == "/dev/sda"
    assert row["health"] == "ok"
    assert row["temp_class"] == "ok"
    assert row["temperature"] is None
    assert row["test_status"] == "idle"
    assert row["scan_status"] == "idle"
    assert row["reallocated"] is None


def test_dashboard_status_uses_latest_attrs(db, config_dict):
    with session_scope() as s:
        did = _seed_drive(s)
        s.add(SmartAttribute(drive_id=did, name="Temperature_Celsius", raw=55))
        s.add(SmartAttribute(drive_id=did, name="Reallocated_Sector_Ct", raw=2))
    out = poll_handling.get_dashboard_status()
    row = out[0]
    assert row["temperature"] == 55
    assert row["temp_class"] == "warn"
    assert row["reallocated"] == 2
    assert row["health"] == "warn"


def test_dashboard_status_critical_temp(db, config_dict):
    with session_scope() as s:
        did = _seed_drive(s)
        s.add(SmartAttribute(drive_id=did, name="Temperature_Celsius", raw=65))
    assert poll_handling.get_dashboard_status()[0]["temp_class"] == "bad"


def test_dashboard_status_reports_active_test_and_scan(db, config_dict):
    with session_scope() as s:
        did = _seed_drive(s)
        s.add(SelfTestResult(drive_id=did, test_type="short", status="IN_PROGRESS"))
    row = poll_handling.get_dashboard_status()[0]
    assert row["test"] == "short"
    assert row["test_status"] == "in_progress"


# ---- _mount_point -----------------------------------------------------------


def test_mount_point_found(monkeypatch):
    parts = [
        SimpleNamespace(device="/dev/sda1", mountpoint="/mnt/data"),
        SimpleNamespace(device="/dev/sda2", mountpoint="/boot"),
    ]
    monkeypatch.setattr(psutil, "disk_partitions", lambda all=False: parts)
    assert poll_handling._mount_point("/dev/sda1") == "/mnt/data"


def test_mount_point_unmounted(monkeypatch):
    monkeypatch.setattr(psutil, "disk_partitions", lambda all=False: [])
    assert poll_handling._mount_point("/dev/sdb") is None
