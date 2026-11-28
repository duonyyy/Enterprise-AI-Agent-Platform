"""Bounded read recovery does not retry ambiguous writes or mask integrity errors."""

from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest
from agent_runtime.application.run_data import RunDataStorageUnavailableError
from agent_runtime.persistence.object_store import S3SigV4ObjectStore


@pytest.mark.anyio
async def test_one_interrupted_get_retries_and_verifies_bytes() -> None:
    calls = 0
    data = b'{"answer":"fixture"}'

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        assert request.method == "GET"
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError("stale pooled connection")
        return httpx.Response(200, content=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = S3SigV4ObjectStore(
            endpoint="https://storage.test",
            access_key="fixture",
            secret_key="fixture",
            bucket="fixture",
            client=client,
        )
        result = await store.get_verified_object(
            bucket="fixture",
            key="object",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            expected_size_bytes=len(data),
            max_bytes=100,
        )
        assert result == data and calls == 2


@pytest.mark.anyio
async def test_ambiguous_put_is_not_retried() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        raise httpx.RemoteProtocolError("write outcome is unknown")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = S3SigV4ObjectStore(
            endpoint="https://storage.test",
            access_key="fixture",
            secret_key="fixture",
            bucket="fixture",
            client=client,
        )
        with pytest.raises(RunDataStorageUnavailableError):
            await store.put_immutable_object(
                bucket="fixture",
                key="object",
                data=b"{}",
                expected_sha256=hashlib.sha256(b"{}").hexdigest(),
            )
        assert calls == ["PUT"]


@pytest.mark.anyio
async def test_object_read_has_one_overall_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = S3SigV4ObjectStore(
            endpoint="https://storage.test",
            access_key="fixture",
            secret_key="fixture",
            bucket="fixture",
            client=client,
            timeout_seconds=0.05,
        )
        with pytest.raises(RunDataStorageUnavailableError, match="timed out"):
            await store.get_verified_object(bucket="fixture", key="object")
