"""Poll logic for drive health jobs.

Scheduled entrypoints are async; heavy blocking work (pySMART, subprocess,
badblocks, DB writes) runs via asyncio.to_thread. discover_drives is cached
for 60s, so the pyudev walk + DB commit happens at most once a minute even
when several jobs call it in the same tick."""


from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import psutil
import pyudev
from pySMART import Device # stupid fucking naming for this package btw
from sqlalchemy import delete, func
from sqlmodel import Session, select

from config_handling import get as get_config
from database_handling import session_scope
from models import BadBlockScan, Drive, SelfTestResult, SmartAttribute, utcnow

log = logging.getLogger("uvicorn.error")

SMARTCTL = "smartctl"
BADBLOCKS = "badblocks"
HDPARM = "hdparm"
NVME = "nvme"

# discovery is re-run at most once per TTL; a pyudev walk + DB commit on every
# job tick is wasted work when nothing changed
_DISCOVER_CACHE_TTL = 60  # seconds
_discover_cache: tuple[float, list[dict]] | None = None


def _run_cmd(*args: str) -> subprocess.CompletedProcess | None:
    """Run a command; None if the binary is not installed."""
    try:
        return subprocess.run(list(args), capture_output=True, text=True)
    except FileNotFoundError:
        log.warning("command not found: %s", args[0])
        return None

# canonical key -> aliases used by different drive families and ages;
# ATA, SATA SSD, and NVMe report the same thing under different names
_ATTR_ALIASES = {
    "temperature_celsius": "Temperature_Celsius",
    "temperature": "Temperature_Celsius",
    "temp": "Temperature_Celsius",
    "temperature_internal": "Temperature_Celsius",
    "airflow_temperature_celsius": "Temperature_Celsius",
    "temperature_case": "Temperature_Celsius",
    "composite_temperature": "Temperature_Celsius",
    "reallocated_sector_ct": "Reallocated_Sector_Ct",
    "reallocated_sector_count": "Reallocated_Sector_Ct",
    "reallocated_event_count": "Reallocated_Sector_Ct",
    "current_pending_sector": "Current_Pending_Sector",
    "current_pending_sector_count": "Current_Pending_Sector",
    "current_pending_sectors": "Current_Pending_Sector",
    "offline_uncorrectable": "Offline_Uncorrectable",
    "offline_uncorrectable_sectors": "Offline_Uncorrectable",
    "reported_uncorrectable": "Offline_Uncorrectable",
    "uncorrectable_sectors": "Offline_Uncorrectable",
    "spin_retry_count": "Spin_Retry_Count",
    "power_on_hours": "Power_On_Hours",
    "power_on_time": "Power_On_Hours",
    "percentage_used": "Percentage_Used",
    "available_spare": "Available_Spare",
    "available_spare_space": "Available_Spare",
    "media_and_data_integrity_errors": "Media_Errors",
    "media_and_data_integrity_error": "Media_Errors",
    "media_errors": "Media_Errors",
    "unsafe_shutdowns": "Unsafe_Shutdowns",
    "power_cycles": "Power_Cycles",
    "critical_warning": "Critical_Warning",
}


def _canonical_attr(name: str) -> str:
    """Map any SMART attribute name to a canonical key, else return it lower."""
    key = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return _ATTR_ALIASES.get(key, key)


def _mount_point(node: str) -> str | None:
    """Return the mountpoint for a device node, or None if not mounted."""
    real = os.path.realpath(node)
    for part in psutil.disk_partitions(all=False):
        if os.path.realpath(part.device) == real:
            return part.mountpoint
    return None


def discover_drives() -> list[dict]:
    """Cached facade over _discover_drives; keeps the pyudev walk + DB
    commit at most once per _DISCOVER_CACHE_TTL seconds."""
    global _discover_cache
    now = time.monotonic()
    if _discover_cache is not None and now - _discover_cache[0] < _DISCOVER_CACHE_TTL:
        return _discover_cache[1]
    drives = _discover_drives()
    _discover_cache = (now, drives)
    return drives


def _discover_drives() -> list[dict]:
    """Upsert block devices pyudev reports as disks; return plain snapshots.

    Plain dicts are returned so callers never touch detached ORM rows;
    the session is closed before these are used.
    """
    drives: list[dict] = []
    ctx = pyudev.Context()
    with session_scope() as session:
        for dev in ctx.list_devices(subsystem="block", DEVTYPE="disk"):
            node = dev.device_node
            if not node:
                continue
            drive = session.exec(select(Drive).where(Drive.device == node)).first()
            if drive is None:
                drive = Drive(device=node)
                session.add(drive)
                session.flush()  # assign id before the snapshot is taken
            drive.model = dev.get("ID_MODEL") or drive.model
            drive.serial = dev.get("ID_SERIAL") or drive.serial
            drive.firmware = dev.get("ID_REVISION") or drive.firmware
            drive.transport = dev.get("ID_TRANSPORT") or dev.get("ID_BUS") or drive.transport
            rotation = dev.get("ID_ROTATION_RATE")
            if rotation is not None:
                try:
                    drive.rotation_rate = int(rotation)
                except ValueError:
                    pass
            size = dev.attributes.get("size")
            if size is not None:
                try:
                    drive.capacity_bytes = int(size) * 512
                except ValueError:
                    pass
            drive.mount_point = _mount_point(node)
            drives.append({
                "id": drive.id,
                "device": node,
                "mount_point": drive.mount_point,
                "transport": drive.transport or "",
            })
        session.commit()
    return drives


def _coerce_int(value) -> int | None:
    """Attribute raw/current values arrive as int; guard against strings."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# NVMe attributes arrive as fields on pySMART's if_attributes object, not
# as Attribute objects; map them onto our canonical names
_NVME_ATTR_FIELDS = (
    ("criticalWarning", "Critical_Warning"),
    ("temperature", "Temperature_Celsius"),
    ("availableSpare", "Available_Spare"),
    ("percentageUsed", "Percentage_Used"),
    ("powerOnHours", "Power_On_Hours"),
    ("unsafeShutdowns", "Unsafe_Shutdowns"),
    ("integrityErrors", "Media_Errors"),
    ("powerCycles", "Power_Cycles"),
)


def _nvme_attrs(if_attributes) -> list[SmartAttribute]:
    """Build SmartAttribute rows from a pySMART NVMe if_attributes object."""
    rows: list[SmartAttribute] = []
    if if_attributes is None:
        return rows
    for field, canonical in _NVME_ATTR_FIELDS:
        value = _coerce_int(getattr(if_attributes, field, None))
        if value is not None:
            rows.append(SmartAttribute(name=canonical, raw=value))
    return rows


def _smart_attrs(node: str) -> list[SmartAttribute] | None:
    """Read SMART attributes for one node via pySMART. None if unavailable."""
    try:
        device = Device(node)
    except Exception:
        log.warning("pysmart failed for %s", node, exc_info=True)
        return None
    rows: list[SmartAttribute] = []
    # ATA/SATA expose Attribute objects; NVMe/SCSI return [None]*256 here
    for attr in getattr(device, "attributes", None) or []:
        if attr is None:
            continue
        name = getattr(attr, "name", None)
        if name is None:
            continue
        rows.append(
            SmartAttribute(
                name=_canonical_attr(name),
                normalized=_coerce_int(getattr(attr, "current", None)),
                worst=_coerce_int(getattr(attr, "worst", None)),
                threshold=_coerce_int(getattr(attr, "threshold", None)),
                raw=_coerce_int(getattr(attr, "raw", None)),
                flags=getattr(attr, "flags", None),
            )
        )
    if not rows:
        rows = _nvme_attrs(getattr(device, "if_attributes", None))
    # overall self-assessment; marks critical even when attrs look ok
    assessment = getattr(device, "assessment", None)
    if assessment:
        rows.append(SmartAttribute(
            name="Overall_Health",
            raw=1 if str(assessment).upper() == "FAIL" else 0,
        ))
    return rows or None


def _poll_smart_node(drive: dict) -> None:
    """Read SMART attrs for one drive and persist them as a snapshot."""
    node = drive["device"]
    rows = _smart_attrs(node)
    if not rows:
        log.warning("no SMART data for %s", node)
        return
    ts = utcnow()
    with session_scope() as session:
        for attr in rows:
            attr.drive_id = drive["id"]
            attr.timestamp = ts
            session.add(attr)
        session.commit()
    _sync_selftest_status(drive)


async def poll_smart() -> None:
    """Interval job; persist fresh SMART attributes for every drive."""
    for drive in discover_drives():
        await asyncio.to_thread(_poll_smart_node, drive)


def _keep_alive_mount(mountpoint: str) -> None:
    """Read the first small file found on a mount; spins the drive up."""
    root = Path(mountpoint)
    try:
        for entry in root.iterdir():
            if entry.is_file() and entry.stat().st_size > 0:
                with entry.open("rb") as fh:
                    fh.read(4096)
                return
    except OSError:
        log.warning("keep-alive read failed on %s", mountpoint, exc_info=True)


async def keep_alive_read() -> None:
    """Interval job; touch every mounted drive to keep platters spun up."""
    for drive in discover_drives():
        if drive["mount_point"]:
            await asyncio.to_thread(_keep_alive_mount, drive["mount_point"])


def _selftest_json(node: str) -> dict | None:
    """Parse smartctl -l selftest -j output. Exit codes are unreliable here:
    bit 7 (128) is set whenever the log contains error records, which is
    exactly the case we want to see. None if the binary is missing or the
    output is not valid JSON."""
    proc = _run_cmd(SMARTCTL, "-l", "selftest", "-j", node)
    if proc is None:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _map_selftest_status(raw) -> str:
    """smartctl >= 7.0 wraps status in {value, passed, string}; older
    versions used a bare string."""
    if isinstance(raw, dict):
        raw = raw.get("string") or ""
    lower = (raw or "").lower()
    if "in progress" in lower:
        return "IN_PROGRESS"
    if "completed without error" in lower or "completed no error" in lower:
        return "PASSED"
    return "FAILED"


def _sync_selftest_status(drive: dict) -> None:
    """Reconcile the newest queued self-test against the smartctl log."""
    data = _selftest_json(drive["device"])
    if not data:
        return
    table = data.get("json_log", {}).get("selftest", {}).get("table")
    if not table:
        return
    latest = table[0]
    with session_scope() as session:
        row = session.exec(
            select(SelfTestResult)
            .where(
                SelfTestResult.drive_id == drive["id"],
                SelfTestResult.status.in_(("QUEUED", "IN_PROGRESS")),
            )
            .order_by(SelfTestResult.started_at.desc())
        ).first()
        if row is None:
            return
        row.status = _map_selftest_status(latest.get("status", ""))
        if row.status != "IN_PROGRESS":
            row.finished_at = utcnow()
        session.add(row)
        session.commit()


def _has_active_test(drive_id: int, test_type: str) -> bool:
    """True if a self-test for this drive is queued or in progress."""
    with session_scope() as session:
        stmt = (
            select(SelfTestResult)
            .where(
                SelfTestResult.drive_id == drive_id,
                SelfTestResult.test_type == test_type,
                SelfTestResult.status.in_(("QUEUED", "IN_PROGRESS")),
            )
            .limit(1)
        )
        return session.exec(stmt).first() is not None


def _trigger_test(drive: dict, test_type: str) -> None:
    """Trigger a smartctl self-test; skip if a test for it is already running."""
    node = drive["device"]
    if _has_active_test(drive["id"], test_type):
        log.info("skipping %s self-test on %s: already running", test_type, node)
        return
    proc = _run_cmd(SMARTCTL, "-t", test_type, node)
    if proc is None or proc.returncode != 0:
        if proc is not None:
            log.warning("smartctl -t %s failed on %s: %s",
                        test_type, node, proc.stderr.strip())
        return
    with session_scope() as session:
        session.add(SelfTestResult(drive_id=drive["id"], test_type=test_type, status="QUEUED"))
        session.commit()


async def _run_tests(test_type: str) -> None:
    for drive in discover_drives():
        await asyncio.to_thread(_trigger_test, drive, test_type)


async def run_short_tests() -> None:
    """Interval job; run the SMART short self-test on every drive."""
    await _run_tests("short")


async def run_long_tests() -> None:
    """Interval job; run the SMART long self-test on every drive."""
    await _run_tests("long")


_BADBLOCKS_RE = re.compile(r"(\d+)\s+bad blocks")


def _badblocks_proc(node: str) -> subprocess.Popen | None:
    """Start a badblocks scan; None if the binary is missing.

    stderr is merged into stdout so the summary line is found regardless of
    which stream the version writes it to."""
    try:
        return subprocess.Popen(
            [BADBLOCKS, "-s", "-v", node],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError:
        log.warning("command not found: %s", BADBLOCKS)
        return None


def _drain_tail(proc: subprocess.Popen, max_chars: int = 8192) -> str:
    """Drain badblocks output keeping only the tail.

    A full -s -v scan on a large disk can emit tens of MB of progress text;
    we only need the summary at the end, so drop everything but the tail."""
    tail = bytearray()
    while True:
        chunk = proc.stdout.read(65536)
        if not chunk:
            break
        tail.extend(chunk)
        if len(tail) > max_chars:
            del tail[: len(tail) - max_chars]
    return tail.decode("utf-8", "replace")


def _run_badblocks(drive: dict) -> None:
    """Read-only badblocks scan for one drive; record outcome in the DB."""
    node = drive["device"]
    with session_scope() as session:
        active = (
            session.exec(
                select(BadBlockScan)
                .where(
                    BadBlockScan.drive_id == drive["id"],
                    BadBlockScan.status == "RUNNING",
                )
                .limit(1)
            ).first()
            is not None
        )
        if active:
            log.info("skipping badblocks on %s: a scan is already running", node)
            return
        scan = BadBlockScan(drive_id=drive["id"], status="RUNNING")
        session.add(scan)
        session.commit()  # persist RUNNING before the long scan so it holds no lock
        scan_id = scan.id

    proc = _badblocks_proc(node)
    if proc is None:
        bad, status, error = None, "FAILED", "badblocks not found"
    else:
        output = _drain_tail(proc)
        proc.wait()
        match = _BADBLOCKS_RE.search(output)
        bad = int(match.group(1)) if match else None
        if bad is not None:
            status, error = "COMPLETED", None
        else:
            status, error = "FAILED", output.strip()[-500:] or f"exit {proc.returncode}"

    with session_scope() as session:
        scan = session.get(BadBlockScan, scan_id)
        if scan is not None:
            scan.finished_at = utcnow()
            scan.status = status
            scan.bad_blocks = bad
            scan.error = error
            session.add(scan)
            session.commit()


async def run_badblock_scans() -> None:
    """Interval job; read-only badblocks scan of every drive."""
    for drive in discover_drives():
        await asyncio.to_thread(_run_badblocks, drive)


def _prune_old_rows() -> None:
    """Drop SMART snapshots older than the retention window so the snapshot
    table stays bounded on a low-RAM device. Sync; call from a worker."""
    days = get_config()["database"]["retention_days"]
    cutoff = utcnow() - timedelta(days=days)
    with session_scope() as session:
        session.execute(delete(SmartAttribute).where(SmartAttribute.timestamp < cutoff))


async def prune_smart_attributes() -> None:
    """Daily job; prune old SMART snapshots off the event loop."""
    await asyncio.to_thread(_prune_old_rows)


def _is_nvme(node: str) -> bool:
    return os.path.basename(node).startswith("nvme")


def _is_loop_or_mapped(node: str) -> bool:
    base = os.path.basename(node)
    return base.startswith(("loop", "dm-", "ram", "zram", "md"))


def _apply_hdparm(node: str) -> None:
    """Disable spin-down and APM for an ATA/USB node; needs root."""
    proc = _run_cmd(HDPARM, "-B", "255", "-S", "0", node)
    if proc is not None and proc.returncode != 0:
        log.warning("hdparm failed on %s: %s", node, proc.stderr.strip())


def _apply_nvme_power(node: str) -> None:
    """Keep NVMe drives awake; force power state 0 and disable APST."""
    proc = _run_cmd(NVME, "set-feature", node, "-n", "0xffffffff", "-f", "0x02", "-v", "0")
    if proc is not None and proc.returncode != 0:
        log.warning("nvme power state failed on %s: %s", node, proc.stderr.strip())
    proc = _run_cmd(NVME, "set-feature", node, "-n", "0xffffffff", "-f", "0x0c", "-v", "0")
    if proc is not None and proc.returncode != 0:
        log.warning("nvme APST disable failed on %s: %s", node, proc.stderr.strip())


def _apply_power(drive: dict) -> None:
    """Route a drive to the power command matching its transport."""
    node = drive["device"]
    if _is_loop_or_mapped(node):
        return
    if _is_nvme(node) or drive.get("transport") == "nvme":
        _apply_nvme_power(node)
    else:
        _apply_hdparm(node)


def _latest_attrs_by_name(drive_id: int, session: Session) -> dict[str, int | None]:
    """Most recent raw value per SMART attribute name for one drive.

    Fetches only the newest row per name (max id subquery) instead of the
    full snapshot history; the dashboard calls this every refresh."""
    latest: dict[str, int | None] = {}
    newest_ids = (
        select(func.max(SmartAttribute.id))
        .where(SmartAttribute.drive_id == drive_id)
        .group_by(SmartAttribute.name)
    )
    rows = session.exec(
        select(SmartAttribute)
        .where(SmartAttribute.id.in_(newest_ids))
        .order_by(SmartAttribute.id.desc())
    ).all()
    for r in rows:
        key = _canonical_attr(r.name)
        if key not in latest:
            latest[key] = r.raw
    return latest


def _drive_health_label(attrs: dict[str, int | None]) -> str:
    """ok / warn / critical from configurable thresholds, ATA + NVMe aware."""
    h = get_config()["health"]
    temp = attrs.get("Temperature_Celsius") or 0
    realloc = attrs.get("Reallocated_Sector_Ct") or 0
    uncorrectable = attrs.get("Offline_Uncorrectable") or 0
    pending = attrs.get("Current_Pending_Sector") or 0
    spin_retry = attrs.get("Spin_Retry_Count") or 0
    used = attrs.get("Percentage_Used") or 0
    media = attrs.get("Media_Errors") or 0
    unsafe = attrs.get("Unsafe_Shutdowns") or 0
    overall = attrs.get("Overall_Health") or 0
    critical_warning = attrs.get("Critical_Warning") or 0
    spare = attrs.get("Available_Spare")
    spare = spare if spare is not None else 100  # no spare data = assume full

    critical = (
        overall == 1
        or critical_warning != 0
        or realloc >= h["reallocated_critical"]
        or uncorrectable >= h["uncorrectable_critical"]
        or temp >= h["temp_critical_c"]
        or used >= h["percentage_used_critical"]
        or media >= h["media_errors_critical"]
        or spare <= h["available_spare_critical"]
    )
    warn = (
        realloc >= h["reallocated_warn"]
        or pending >= h["pending_warn"]
        or uncorrectable >= h["uncorrectable_warn"]
        or spin_retry >= h["spin_retry_warn"]
        or temp >= h["temp_warn_c"]
        or used >= h["percentage_used_warn"]
        or media >= h["media_errors_warn"]
        or unsafe >= h["unsafe_shutdowns_warn"]
        or spare <= h["available_spare_warn"]
    )
    if critical:
        return "critical"
    if warn:
        return "warn"
    return "ok"


def get_dashboard_status() -> list[dict]:
    """Read model for the dashboard: drives + latest smart + active jobs."""
    h = get_config()["health"]
    out: list[dict] = []
    with session_scope() as session:
        drives = session.exec(select(Drive)).all()
        for d in drives:
            attrs = _latest_attrs_by_name(d.id, session)
            temp = attrs.get("Temperature_Celsius")
            if temp is None:
                temp_class = "ok"
            elif temp >= h["temp_critical_c"]:
                temp_class = "bad"
            elif temp >= h["temp_warn_c"]:
                temp_class = "warn"
            else:
                temp_class = "ok"
            active_test = session.exec(
                select(SelfTestResult)
                .where(
                    SelfTestResult.drive_id == d.id,
                    SelfTestResult.status.in_(("QUEUED", "IN_PROGRESS")),
                )
                .order_by(SelfTestResult.started_at.desc())
            ).first()
            active_scan = session.exec(
                select(BadBlockScan)
                .where(
                    BadBlockScan.drive_id == d.id,
                    BadBlockScan.status == "RUNNING",
                )
                .order_by(BadBlockScan.started_at.desc())
            ).first()
            out.append({
                "device": d.device,
                "model": d.model,
                "capacity_bytes": d.capacity_bytes,
                "mount_point": d.mount_point,
                "temperature": temp,
                "temp_class": temp_class,
                "reallocated": attrs.get("Reallocated_Sector_Ct"),
                "pending": attrs.get("Current_Pending_Sector"),
                "uncorrectable": attrs.get("Offline_Uncorrectable"),
                "power_on_hours": attrs.get("Power_On_Hours"),
                "percentage_used": attrs.get("Percentage_Used"),
                "media_errors": attrs.get("Media_Errors"),
                "unsafe_shutdowns": attrs.get("Unsafe_Shutdowns"),
                "available_spare": attrs.get("Available_Spare"),
                "health": _drive_health_label(attrs),
                "test": active_test.test_type if active_test else None,
                "test_status": active_test.status.lower() if active_test else "idle",
                "scan_status": "running" if active_scan else "idle",
            })
    return out


async def apply_power_settings() -> None:
    """One-time config on startup; keep mounted drives spun up."""
    for drive in discover_drives():
        await asyncio.to_thread(_apply_power, drive)


# guard against overlapping manual test-all runs from rapid button clicks
_RUN_ALL_TESTS_LOCK = asyncio.Lock()


async def run_all_tests() -> None:
    """Instant diagnostic run per drive: SMART/temp poll, short self-test,
    badblocks. Skips a stage if that drive already has it in flight, and
    refuses to start while a manual run is already in progress."""
    if _RUN_ALL_TESTS_LOCK.locked():
        log.info("skipping test-all: a manual run is already in progress")
        return
    async with _RUN_ALL_TESTS_LOCK:
        drives = await asyncio.to_thread(discover_drives)
        for drive in drives:
            await asyncio.to_thread(_poll_smart_node, drive)
            await asyncio.to_thread(_trigger_test, drive, "short")
            await asyncio.to_thread(_run_badblocks, drive)
