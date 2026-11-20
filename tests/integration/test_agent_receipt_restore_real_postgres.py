"""Publish receipt replay after restoring a fixture-owned PostgreSQL database."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from control_api.core.database import get_db_session
from control_api.main import app
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from test_postgres_fixture import (
    backup_database,
    database_url,
    restore_database,
    temporary_database,
)

from tests.integration.test_agent_publish_real_postgres import (
    _make_sa_url,
    _make_spec,
)

pytest_plugins = ("tests.integration.test_agent_publish_real_postgres",)


@pytest.mark.integration
def test_publish_receipt_replay_after_database_restore(
    publish_test_env: dict[str, Any], postgres_cluster: dict[str, Any], tmp_path: Path
) -> None:
    env = publish_test_env
    workspace_id = env["seeded"]["ws_1a"]
    headers = {"Authorization": f"Bearer {env['tokens']['dev_1a']}"}
    source_db = psycopg.conninfo.conninfo_to_dict(env["owner_url"])["dbname"]
    source_cluster = {**postgres_cluster, "primary_db": source_db}

    async def scenario() -> None:
        source_engine = create_async_engine(env["sa_url"], poolclass=NullPool)
        engines = [source_engine]

        def use_engine(engine: Any) -> None:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

            async def override_db():
                async with factory() as session:
                    yield session

            app.dependency_overrides[get_db_session] = override_db

        try:
            use_engine(source_engine)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://testserver"
            ) as client:
                created = await client.post(
                    f"/v1/workspaces/{workspace_id}/agents",
                    headers=headers,
                    json={
                        "slug": "restore-agent",
                        "display_name": "Restore Agent",
                        "spec": _make_spec("restore-agent", env["deps"]),
                    },
                )
                assert created.status_code == 201, created.text
                agent_id = created.json()["agent_id"]
                endpoint = f"/v1/workspaces/{workspace_id}/agents/{agent_id}/publish"
                publish_headers = {**headers, "Idempotency-Key": "restore-receipt-key"}
                original = await client.post(
                    endpoint, headers=publish_headers, json={"expected_revision": 1}
                )
                assert original.status_code == 200, original.text

                dump_path = tmp_path / "agent-receipt.dump"
                backup_database(source_cluster, dump_path)
                with temporary_database(postgres_cluster, "receipt_restore") as target:
                    restore_database(postgres_cluster, dump_path, target)
                    restored_owner = database_url(postgres_cluster, target)
                    with psycopg.connect(restored_owner) as conn:
                        before = conn.execute(
                            "SELECT count(*) FROM catalog.agent_publish_receipts WHERE agent_id = %s",
                            (agent_id,),
                        ).fetchone()[0]
                        assert before == 1
                    restored_engine = create_async_engine(
                        _make_sa_url(database_url(postgres_cluster, target, app=True)),
                        poolclass=NullPool,
                    )
                    engines.append(restored_engine)
                    use_engine(restored_engine)
                    replay = await client.post(
                        endpoint, headers=publish_headers, json={"expected_revision": 1}
                    )
                    assert replay.status_code == 200, replay.text
                    assert replay.json() == original.json()
                    with psycopg.connect(restored_owner) as conn:
                        assert (
                            conn.execute(
                                "SELECT count(*) FROM catalog.agent_publish_receipts WHERE agent_id = %s",
                                (agent_id,),
                            ).fetchone()[0]
                            == before
                        )
                        assert (
                            conn.execute(
                                "SELECT count(*) FROM catalog.agent_versions WHERE agent_id = %s",
                                (agent_id,),
                            ).fetchone()[0]
                            == 1
                        )
                    await restored_engine.dispose()
        finally:
            app.dependency_overrides.clear()
            for engine in engines:
                await engine.dispose()

    asyncio.run(scenario())
