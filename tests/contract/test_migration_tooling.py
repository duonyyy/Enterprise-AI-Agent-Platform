"""Regression cases for migration parsing and fixture denial boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import test_postgres_fixture as fixtures
from run_migrations import MigrationError, prepare_migration_sql, write_bootstrap_sql


def test_transaction_wrapper_does_not_rewrite_strings_comments_or_do_body():
    body = "DO $code$ BEGIN RAISE NOTICE 'COMMIT;'; END $code$;"
    quoted = "SELECT 'BEGIN;', E'COMMIT;\\\\', \"COMMIT;\";"
    source = f"-- BEGIN;\n/* COMMIT; /* nested */ */ BEGIN; {body} {quoted} COMMIT;"
    prepared = prepare_migration_sql(source)
    assert body in prepared
    assert quoted in prepared
    assert "-- BEGIN;" in prepared
    assert "/* COMMIT; /* nested */ */" in prepared


@pytest.mark.parametrize(
    "source",
    [
        "BEGIN; SELECT 1;",
        "COMMIT;",
        "BEGIN; BEGIN; COMMIT;",
        "ROLLBACK;",
        "BEGIN; SELECT 1; COMMIT AND CHAIN;",
        "SELECT 'unterminated",
        "DO $x$ BEGIN;",
        "/* unclosed",
    ],
)
def test_unsafe_transaction_structure_is_rejected(source):
    with pytest.raises(MigrationError):
        prepare_migration_sql(source)


def test_fixture_ownership_mismatch_stops_before_restart(monkeypatch):
    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        return json.dumps({fixtures.LABEL: "someone-else"})

    monkeypatch.setattr(fixtures, "docker", command)
    with pytest.raises(RuntimeError, match="unowned"):
        fixtures.restart_cluster({"container_id": "foreign", "run_id": "ours"})
    assert len(calls) == 1
    assert calls[0][0:2] == ("container", "inspect")


def test_docker_exit_one_is_an_error(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "restore failed")
    )
    with pytest.raises(RuntimeError, match="exit 1"):
        fixtures.docker("exec", "fixture", "pg_restore")


def test_restore_refuses_target_not_created_by_fixture(monkeypatch, tmp_path):
    monkeypatch.setattr(fixtures, "verify_owned", lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match="not created"):
        fixtures.restore_database(
            {"owned_databases": set()}, tmp_path / "backup.dump", "existing_db"
        )


def test_bootstrap_cannot_overwrite_canonical_migrations(tmp_path):
    migration = tmp_path / "0001_original.sql"
    migration.write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="must not overwrite"):
        write_bootstrap_sql(tmp_path, migration)
    assert migration.read_text() == "SELECT 1;"
