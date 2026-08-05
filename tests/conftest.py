"""Shared fixtures: in-memory DB, isolated config, import path."""

import copy
import os
import sys

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import config_handling
import database_handling

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def db(monkeypatch):
    """Fresh in-memory SQLite engine per test; no files touch disk."""
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database_handling, "engine", engine)
    yield engine
    # dispose so the StaticPool connection closes here instead of at GC,
    # where sqlite finalization raises an unraisable exception
    engine.dispose()


@pytest.fixture
def cfg_path(monkeypatch, tmp_path):
    """Point config_handling at an isolated file and reset its cache."""
    path = tmp_path / "config.toml"
    monkeypatch.setattr(config_handling, "CONFIG_PATH", path)
    monkeypatch.setattr(config_handling, "_config", None)
    return path


@pytest.fixture
def config_dict(monkeypatch):
    """Seed config_handling with an isolated copy of DEFAULTS."""
    cfg = copy.deepcopy(config_handling.DEFAULTS)
    monkeypatch.setattr(config_handling, "_config", cfg)
    return cfg
