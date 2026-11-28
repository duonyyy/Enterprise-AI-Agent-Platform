"""Real PostgreSQL and MinIO checks for the read-only retention assessment."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from agent_runtime import maintenance
from agent_runtime.persistence.database import set_rls_context
from agent_runtime.persistence.object_store import S3SigV4ObjectStore
from agent_runtime.persistence.run_retention_repository import RunRetentionRepository
from agent_runtime.settings import RuntimeSettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.test_run_admission_real_postgres import (
    TEST_BUCKET,
    _seed_admission_fixture,
    _to_async_pg_url,
    owned_minio_cluster,
    owned_redis_cluster,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def redis_cluster() -> Generator[dict[str, Any], None, None]:
    with owned_redis_cluster() as cluster:
        yield cluster


@pytest.fixture(scope="module")
def minio_cluster() -> Generator[dict[str, Any], None, None]:
    with owned_minio_cluster() as cluster:
        yield cluster


def _terminal(
    owner_url: str,
    fx: dict[str, Any],
    *,
    age_days: int,
    ref: str | None = None,
    conversation_id: str | None = None,
) -> str:
    run_id = str(uuid4())
    ref = (
        ref
        or f"s3://{TEST_BUCKET}/tenants/{fx['t_a']}/workspaces/{fx['w_a1']}/runs/{run_id}/input/{'a' * 64}-10.json"
    )
    with psycopg.connect(owner_url) as conn:
        conn.execute(
            """INSERT INTO runtime.runs
            (id,tenant_id,workspace_id,agent_version_id,initiated_by,external_request_key,
             status,created_at,queue_expires_at,finished_at,request_sha256,input_ref,input_sha256,input_size_bytes,conversation_id)
            VALUES (%s,%s,%s,%s,%s,%s,'succeeded',clock_timestamp()-make_interval(days=>%s),
                clock_timestamp()-make_interval(days=>%s),clock_timestamp()-make_interval(days=>%s),%s,%s,%s,10,%s)""",
            (
                run_id,
                fx["t_a"],
                fx["w_a1"],
                fx["av_no_grants"],
                fx["dev_a"],
                str(uuid4()),
                age_days + 5,
                age_days + 4,
                age_days,
                "b" * 64,
                ref,
                "a" * 64,
                conversation_id,
            ),
        )
    return run_id


def test_content_timeline_and_receipt_holds_are_independent(
    migrated_database: dict[str, Any], redis_cluster: dict[str, Any]
) -> None:
    pg = migrated_database
    fx = _seed_admission_fixture(pg["owner_url"], redis_cluster["url"])
    _terminal(pg["owner_url"], fx, age_days=35)
    _terminal(pg["owner_url"], fx, age_days=100)

    async def scenario() -> None:
        engine = create_async_engine(_to_async_pg_url(pg["app_url"]))
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def assess() -> dict[str, Any]:
            async with factory() as session, session.begin():
                await set_rls_context(session, tenant_id=fx["t_a"], workspace_id=fx["w_a1"])
                return await RunRetentionRepository().evaluate(
                    session, tenant_id=fx["t_a"], workspace_id=fx["w_a1"], limit=100
                )

        try:
            before = await assess()
            assert before["content"]["eligible"] == 2
            assert before["timeline"]["eligible"] == 1
            assert before["receipts"]["expired"] == 2
            with psycopg.connect(pg["owner_url"]) as conn:
                conn.execute(
                    "INSERT INTO governance.retention_policies (tenant_id,workspace_id,data_class,retention_days,legal_hold) VALUES (%s,%s,'run_receipt',30,true)",
                    (fx["t_a"], fx["w_a1"]),
                )
            receipt_held = await assess()
            assert receipt_held["content"]["eligible"] == 2
            assert receipt_held["timeline"]["eligible"] == 1
            assert receipt_held["receipts"]["expired"] == 0
            assert receipt_held["receipts"]["tombstones_retained"] == 2
            with psycopg.connect(pg["owner_url"]) as conn:
                conn.execute(
                    "INSERT INTO governance.retention_policies (tenant_id,workspace_id,data_class,retention_days,legal_hold) VALUES (%s,%s,'run_content',30,true)",
                    (fx["t_a"], fx["w_a1"]),
                )
            content_held = await assess()
            assert content_held["content"]["eligible"] == 0
            assert content_held["timeline"]["eligible"] == 1
            assert content_held["content"]["legal_hold"] == 2
            with psycopg.connect(pg["owner_url"]) as conn:
                conn.execute(
                    "UPDATE governance.retention_policies SET legal_hold=false WHERE workspace_id=%s AND data_class='run_content'",
                    (fx["w_a1"],),
                )
            assert (await assess())["content"]["eligible"] == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_shared_references_and_conversation_latest_finish_are_protected(
    migrated_database: dict[str, Any], redis_cluster: dict[str, Any]
) -> None:
    pg = migrated_database
    fx = _seed_admission_fixture(pg["owner_url"], redis_cluster["url"])
    shared = f"s3://{TEST_BUCKET}/shared-fixture-reference"
    _terminal(pg["owner_url"], fx, age_days=35, ref=shared)
    _terminal(pg["owner_url"], fx, age_days=40, ref=shared)
    conv = str(uuid4())
    with psycopg.connect(pg["owner_url"]) as conn:
        conn.execute(
            "INSERT INTO runtime.conversations (id,tenant_id,workspace_id,status) VALUES (%s,%s,%s,'closed')",
            (conv, fx["t_a"], fx["w_a1"]),
        )
    _terminal(pg["owner_url"], fx, age_days=35, conversation_id=conv)
    _terminal(pg["owner_url"], fx, age_days=5, conversation_id=conv)

    async def scenario() -> None:
        engine = create_async_engine(_to_async_pg_url(pg["app_url"]))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                await set_rls_context(session, tenant_id=fx["t_a"], workspace_id=fx["w_a1"])
                result = await RunRetentionRepository().evaluate(
                    session, tenant_id=fx["t_a"], workspace_id=fx["w_a1"], limit=100
                )
                assert result["scanned"] == 4
                assert result["content"]["eligible"] == 0
                assert result["content"]["shared"] == 2
                assert result["content"]["within_ttl"] == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_real_s3_inventory_is_scoped_bounded_and_read_only(
    migrated_database: dict[str, Any],
    redis_cluster: dict[str, Any],
    minio_cluster: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pg = migrated_database
    fx = _seed_admission_fixture(pg["owner_url"], redis_cluster["url"])

    async def scenario() -> None:
        store = S3SigV4ObjectStore(
            endpoint=minio_cluster["url"],
            access_key=minio_cluster["app_user"],
            secret_key=minio_cluster["app_secret"],
            bucket=TEST_BUCKET,
        )
        engine = create_async_engine(_to_async_pg_url(pg["app_url"]))
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def scoped(t: str, w: str, u: str | None = None) -> AsyncIterator[AsyncSession]:
            async with factory() as session, session.begin():
                await set_rls_context(session, tenant_id=t, workspace_id=w, user_id=u)
                yield session

        try:
            prefix = f"tenants/{fx['t_a']}/workspaces/{fx['w_a1']}/runs/"
            data = b"{}"
            digest = hashlib.sha256(data).hexdigest()
            for suffix in ("one", "two"):
                await store.put_immutable_object(
                    bucket=TEST_BUCKET, key=prefix + suffix, data=data, expected_sha256=digest
                )
            foreign = f"tenants/{fx['t_b']}/workspaces/{fx['w_b1']}/runs/foreign"
            await store.put_immutable_object(
                bucket=TEST_BUCKET, key=foreign, data=data, expected_sha256=digest
            )
            page = await store.list_scoped_run_objects(
                tenant_id=fx["t_a"], workspace_id=fx["w_a1"], limit=1
            )
            assert len(page.objects) == 1 and page.truncated
            assert all(o.key.startswith(prefix) for o in page.objects)
            monkeypatch.setattr(maintenance, "scoped_session", scoped)
            monkeypatch.setattr(maintenance, "create_object_store", lambda _settings: store)
            monkeypatch.setattr(
                maintenance, "get_settings", lambda: RuntimeSettings(_env_file=None)
            )
            report = await maintenance._run_retention_report(
                tenant_id=fx["t_a"], workspace_id=fx["w_a1"], limit=100, start_time=time.monotonic()
            )
            assert report["changed"] == 0
            assert report["orphans"]["scanned"] == 2
            assert report["orphans"]["protected"] == 2  # Less than the 24-hour grace period.
            assert report["orphans"]["eligible"] == 0
            assert report["orphans"]["checkpoint_inventory_complete"] is False
            assert await store.get_verified_object(bucket=TEST_BUCKET, key=prefix + "one") == data
        finally:
            await store.aclose()
            await engine.dispose()

    asyncio.run(scenario())
