"""Owned PostgreSQL 16 Docker fixtures (DEC-ENV-01), identical locally and in CI.

Each session creates a labelled container/volume on a random loopback port.
Never attach to a pre-existing server or consume application connection URLs.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import subprocess
import time
from collections.abc import Generator
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

OWNER_USER = "platform_owner"
APP_USER = "platform_app"
LABEL = "com.enterprise.step01.run"
POSTGRES_IMAGE = "postgres:16-alpine"


def docker(*args: str, timeout: int = 90) -> str:
    result = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        # Arguments can contain fixture credentials, so do not include them.
        raise RuntimeError(
            f"Docker {args[0]} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def verify_owned(cluster: dict[str, Any], *, volume: bool = False) -> None:
    target = cluster["volume"] if volume else cluster["container_id"]
    kind = "volume" if volume else "container"
    fmt = "{{json .Labels}}" if volume else "{{json .Config.Labels}}"
    labels = json.loads(docker(kind, "inspect", "--format", fmt, target))
    if not isinstance(labels, dict) or labels.get(LABEL) != cluster["run_id"]:
        raise RuntimeError(f"Refusing operation on unowned {kind}")


def database_url(cluster: dict[str, Any], db_name: str, *, app: bool = False) -> str:
    return make_conninfo(cluster["app_url"] if app else cluster["owner_url"], dbname=db_name)


def _wait_ready(cluster: dict[str, Any]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(cluster["owner_url"]) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            Event().wait(0.3)
    raise RuntimeError("Owned PostgreSQL fixture did not become ready")


def _start_container(cluster: dict[str, Any]) -> None:
    verify_owned(cluster, volume=True)
    cluster["container_id"] = docker(
        "create",
        "--name",
        cluster["container_name"],
        "--label",
        f"{LABEL}={cluster['run_id']}",
        "--publish",
        "127.0.0.1::5432",
        "--mount",
        f"type=volume,source={cluster['volume']},target=/var/lib/postgresql/data",
        "--env",
        f"POSTGRES_USER={OWNER_USER}",
        "--env",
        f"POSTGRES_PASSWORD={cluster['owner_password']}",
        "--env",
        f"POSTGRES_DB={cluster['primary_db']}",
        POSTGRES_IMAGE,
    )
    verify_owned(cluster)
    docker("start", cluster["container_id"])
    _refresh_connection_urls(cluster)
    _wait_ready(cluster)


def _refresh_connection_urls(cluster: dict[str, Any]) -> None:
    # Docker can assign a different ephemeral host port after restart/recreate.
    bindings = json.loads(
        docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", cluster["container_id"])
    )
    binding = bindings["5432/tcp"][0]
    if binding["HostIp"] != "127.0.0.1":
        raise RuntimeError("Fixture port must be bound to loopback")
    cluster["port"] = int(binding["HostPort"])
    cluster["owner_url"] = make_conninfo(
        host="127.0.0.1",
        port=cluster["port"],
        user=OWNER_USER,
        password=cluster["owner_password"],
        dbname=cluster["primary_db"],
        connect_timeout=3,
    )
    cluster["app_url"] = make_conninfo(
        cluster["owner_url"], user=APP_USER, password=cluster["app_password"]
    )
    cluster["admin_url"] = make_conninfo(cluster["owner_url"], dbname="postgres")


@contextlib.contextmanager
def postgres_fixture_cluster() -> Generator[dict[str, Any], None, None]:
    run_id = uuid4().hex
    evidence_dir = (
        Path(__file__).resolve().parents[1] / "artifacts/evidence/step01-fixtures" / run_id
    )
    evidence_dir.mkdir(parents=True, exist_ok=False)
    cluster: dict[str, Any] = {
        "run_id": run_id,
        "container_name": f"step01-pg-{run_id}",
        "volume": f"step01-pg-{run_id}",
        "primary_db": "step01_primary",
        "owner_password": secrets.token_hex(24),
        "app_password": secrets.token_hex(24),
        "managed": True,
        "evidence_dir": evidence_dir,
        "owned_databases": set(),
    }
    docker("volume", "create", "--label", f"{LABEL}={run_id}", cluster["volume"])
    (evidence_dir / "OWNERSHIP.json").write_text(
        json.dumps(
            {key: cluster[key] for key in ("run_id", "container_name", "volume", "primary_db")},
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        _start_container(cluster)
        with psycopg.connect(cluster["owner_url"], autocommit=True) as conn:
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(APP_USER), sql.Literal(cluster["app_password"]))
            )
        yield cluster
    finally:
        if cluster.get("container_id"):
            verify_owned(cluster)
            docker("rm", "--force", cluster["container_id"])
        verify_owned(cluster, volume=True)
        docker("volume", "rm", cluster["volume"])
        (evidence_dir / "CLEANUP.txt").write_text(
            "PASS: removed owned container and volume only.\n"
        )


def restart_cluster(cluster: dict[str, Any], *, recreate: bool = False) -> None:
    verify_owned(cluster)
    verify_owned(cluster, volume=True)
    if recreate:
        docker("rm", "--force", cluster["container_id"])
        del cluster["container_id"]
        _start_container(cluster)
    else:
        docker("restart", cluster["container_id"])
        _refresh_connection_urls(cluster)
        _wait_ready(cluster)


@contextlib.contextmanager
def temporary_database(cluster: dict[str, Any], prefix: str) -> Generator[str, None, None]:
    verify_owned(cluster)
    if not prefix.isidentifier() or len(prefix) > 20:
        raise ValueError("Invalid fixture database prefix")
    name = f"{prefix}_{uuid4().hex}"
    with psycopg.connect(cluster["admin_url"], autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(OWNER_USER)
            )
        )
        conn.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(name), sql.Literal(cluster["run_id"])
            )
        )
    cluster["owned_databases"].add(name)
    try:
        yield name
    finally:
        verify_owned(cluster)
        with psycopg.connect(cluster["admin_url"], autocommit=True) as conn:
            marker = conn.execute(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=%s",
                (name,),
            ).fetchone()
            if marker != (cluster["run_id"],):
                raise RuntimeError("Refusing to drop unowned fixture database")
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        cluster["owned_databases"].remove(name)


def backup_database(cluster: dict[str, Any], backup_file: Path) -> None:
    verify_owned(cluster)
    backup_file.parent.mkdir(parents=True, exist_ok=True)
    remote = f"/tmp/step01-{uuid4().hex}.dump"
    docker(
        "exec",
        cluster["container_id"],
        "pg_dump",
        "--username",
        OWNER_USER,
        "--dbname",
        cluster["primary_db"],
        "--format",
        "custom",
        "--file",
        remote,
    )
    docker("cp", f"{cluster['container_id']}:{remote}", str(backup_file.resolve()))


def restore_database(cluster: dict[str, Any], backup_file: Path, target_db: str) -> None:
    verify_owned(cluster)
    if target_db not in cluster["owned_databases"]:
        raise RuntimeError("Restore target was not created by this fixture")
    remote = f"/tmp/step01-{uuid4().hex}.dump"
    docker("cp", str(backup_file.resolve()), f"{cluster['container_id']}:{remote}")
    docker(
        "exec",
        cluster["container_id"],
        "pg_restore",
        "--exit-on-error",
        "--single-transaction",
        "--no-owner",
        "--username",
        OWNER_USER,
        "--dbname",
        target_db,
        remote,
    )
