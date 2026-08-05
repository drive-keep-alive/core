"""TOML-backed config. Defaults live in code; config.toml overlays them.

Designed so a future web dashboard can rewrite config.toml and call
reload(); no routes for that exist yet. A missing or broken file falls
back to DEFAULTS so the app always starts.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

log = logging.getLogger("uvicorn.error")

CONFIG_PATH = Path("config.toml")

DEFAULTS: dict = {
    "scheduler": {
        "keep_alive_minutes": 4,
        "smart_poll_minutes": 15,
        "short_test_days": 7,
        "long_test_days": 30,
        "badblocks_days": 30,
    },
    "health": {
        "temp_warn_c": 50,
        "temp_critical_c": 60,
        "reallocated_warn": 1,
        "pending_warn": 1,
        "uncorrectable_warn": 1,
        "spin_retry_warn": 1,
        "reallocated_critical": 10,
        "uncorrectable_critical": 5,
        "percentage_used_warn": 80,
        "percentage_used_critical": 100,
        "media_errors_warn": 1,
        "media_errors_critical": 5,
        "unsafe_shutdowns_warn": 10,
        "available_spare_warn": 10,
        "available_spare_critical": 1,
    },
    "dashboard": {
        "refresh_seconds": 30,
    },
    "database": {
        "retention_days": 7,  # keep SMART snapshots this long; pruned daily
    },
}

_config: dict | None = None


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Overlay values onto base; nested dicts merge recursively."""
    out = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load() -> dict:
    """Read config.toml; on any error fall back to defaults."""
    global _config
    merged = _deep_merge(DEFAULTS, {})
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("rb") as fh:
                raw = tomllib.load(fh)
            merged = _deep_merge(merged, raw)
        except (tomllib.TOMLDecodeError, OSError):
            log.warning("config.toml unreadable; using defaults", exc_info=True)
    _config = merged
    return _config


def get() -> dict:
    """Return the cached config, loading it on first use."""
    global _config
    if _config is None:
        load()
    return _config


def reload() -> dict:
    """Re-read config.toml; call after a future dashboard writes it."""
    return load()
