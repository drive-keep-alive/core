"""SQLModel schemas for drive health data persisted to SQLite."""

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Current UTC time as a naive datetime.

    Avoids the deprecated datetime.utcnow() while keeping stored values
    timezone-naive, so new rows match the format of existing data."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Drive(SQLModel, table=True):
    __tablename__ = "drives"

    id: int | None = Field(default=None, primary_key=True)
    device: str = Field(index=True, unique=True)  # /dev/sda
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None
    capacity_bytes: int | None = None
    transport: str | None = None  # SATA / NVMe / USB
    rotation_rate: int | None = None  # 0 = SATA SSD; NVMe leaves this None
    mount_point: str | None = None
    first_seen: datetime = Field(default_factory=utcnow)


class SmartAttribute(SQLModel, table=True):
    __tablename__ = "smart_attributes"

    id: int | None = Field(default=None, primary_key=True)
    drive_id: int = Field(foreign_key="drives.id", index=True)
    timestamp: datetime = Field(default_factory=utcnow, index=True)
    name: str = Field(index=True)
    normalized: int | None = None
    worst: int | None = None
    threshold: int | None = None
    raw: int | None = None
    flags: str | None = None


class SelfTestResult(SQLModel, table=True):
    __tablename__ = "self_tests"

    id: int | None = Field(default=None, primary_key=True)
    drive_id: int = Field(foreign_key="drives.id", index=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    test_type: str  # short / long
    status: str  # QUEUED / IN_PROGRESS / PASSED / FAILED
    error: str | None = None


class BadBlockScan(SQLModel, table=True):
    __tablename__ = "bad_block_scans"

    id: int | None = Field(default=None, primary_key=True)
    drive_id: int = Field(foreign_key="drives.id", index=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str  # RUNNING / COMPLETED / FAILED
    bad_blocks: int | None = None
    error: str | None = None
