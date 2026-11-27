"""Subprocess worker helper for process-separated 06-A durability tests (R06A-06)."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from agent_runtime.application.run_data_service import RunDataService
from agent_runtime.auth import RuntimePrincipal
from agent_runtime.persistence.database import set_rls_context
from agent_runtime.persistence.object_store import S3SigV4ObjectStore
from agent_runtime.persistence.run_data_repository import RunDataRepository
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _async_pg_url(conninfo: str) -> URL:
    parts = conninfo_to_dict(conninfo)
    return URL.create(
        "postgresql+asyncpg",
        username=str(parts["user"]),
        password=str(parts["password"]),
        host=str(parts["host"]),
        port=int(parts["port"]),
        database=str(parts["dbname"]),
    )


async def _run_write(payload: dict) -> dict:
    engine = create_async_engine(_async_pg_url(payload["app_url"]), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = S3SigV4ObjectStore(
        endpoint=payload["minio_url"],
        access_key=payload["app_user"],
        secret_key=payload["app_secret"],
        bucket=payload["bucket"],
    )
    service = RunDataService(object_store=store, bucket=payload["bucket"])
    repo = RunDataRepository()
    principal = RuntimePrincipal(
        subject="creator",
        user_id=payload["user_id"],
        tenant_id=payload["tenant_id"],
        workspace_id=payload["workspace_id"],
        roles=("developer",),
        scopes=frozenset({"run:start", "run:read"}),
    )
    try:
        async with factory() as session, session.begin():
            await set_rls_context(
                session,
                tenant_id=payload["tenant_id"],
                workspace_id=payload["workspace_id"],
                user_id=payload["user_id"],
            )
            await repo.create_conversation(
                session,
                tenant_id=payload["tenant_id"],
                workspace_id=payload["workspace_id"],
                creator_user_id=payload["user_id"],
                conversation_id=payload["conversation_id"],
            )

        staged = await service.stage_run_input(
            principal=principal,
            run_id=payload["run_id"],
            agent_version_id=payload["agent_version_id"],
            conversation_id=payload["conversation_id"],
            inputs=payload["inputs"],
            attachments=payload["attachments"],
        )
        async with factory() as session, session.begin():
            await set_rls_context(
                session,
                tenant_id=payload["tenant_id"],
                workspace_id=payload["workspace_id"],
                user_id=payload["user_id"],
            )
            await service.activate_staged_input(
                session,
                principal=principal,
                run_id=payload["run_id"],
                agent_version_id=payload["agent_version_id"],
                external_request_key=f"proc-a-{payload['run_id']}",
                staged=staged,
                conversation_id=payload["conversation_id"],
            )
        return {
            "pid": os.getpid(),
            "sha256": staged.receipt.sha256,
            "size_bytes": staged.receipt.size_bytes,
        }
    finally:
        await store.aclose()
        await engine.dispose()


async def _run_read(payload: dict) -> dict:
    engine = create_async_engine(_async_pg_url(payload["app_url"]), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = S3SigV4ObjectStore(
        endpoint=payload["minio_url"],
        access_key=payload["app_user"],
        secret_key=payload["app_secret"],
        bucket=payload["bucket"],
    )
    service = RunDataService(object_store=store, bucket=payload["bucket"])
    principal = RuntimePrincipal(
        subject="creator",
        user_id=payload["user_id"],
        tenant_id=payload["tenant_id"],
        workspace_id=payload["workspace_id"],
        roles=("developer",),
        scopes=frozenset({"run:read"}),
    )
    try:
        async with factory() as session, session.begin():
            await set_rls_context(
                session,
                tenant_id=payload["tenant_id"],
                workspace_id=payload["workspace_id"],
                user_id=payload["user_id"],
            )
            auth = await service.authorize_run_input_read(
                session,
                principal=principal,
                run_id=payload["run_id"],
            )
        manifest = await service.load_authorized_manifest(auth)
        return {
            "pid": os.getpid(),
            "sha256": auth.expected_sha256,
            "size_bytes": auth.expected_size_bytes,
            "inputs": manifest["inputs"],
            "attachments": manifest["attachments"],
        }
    finally:
        await store.aclose()
        await engine.dispose()


if __name__ == "__main__":
    mode = sys.argv[1]
    data = json.loads(sys.stdin.read())
    result = asyncio.run(_run_write(data) if mode == "write" else _run_read(data))
    print(json.dumps(result))
