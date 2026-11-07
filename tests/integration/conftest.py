"""Owned PostgreSQL 16 fixtures; never consume application database URLs."""

from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from run_migrations import run_migrations
from test_postgres_fixture import database_url, postgres_fixture_cluster, temporary_database


@pytest.fixture(scope="session")
def postgres_cluster() -> Generator[dict[str, Any], None, None]:
    with postgres_fixture_cluster() as cluster:
        yield cluster


@pytest.fixture(scope="session")
def migrated_database(postgres_cluster: dict[str, Any]) -> dict[str, Any]:
    run_migrations(postgres_cluster["owner_url"], SCRIPTS_DIR.parent / "database/migrations")
    return postgres_cluster


@pytest.fixture
def isolated_database(postgres_cluster):
    with temporary_database(postgres_cluster, "migration") as name:
        yield {
            **postgres_cluster,
            "primary_db": name,
            "owner_url": database_url(postgres_cluster, name),
            "app_url": database_url(postgres_cluster, name, app=True),
        }
