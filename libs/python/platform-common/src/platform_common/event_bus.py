"""Durable event publication ports.

The default runtime implementation writes to the transactional PostgreSQL
outbox. A return value of ``True`` means the row is durable, never that a
message was merely accepted by an in-process list.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class EventBusConfigurationError(RuntimeError):
    """Raised when a durable event transport has not been configured."""


class EventBus(ABC):
    @abstractmethod
    async def publish(
        self,
        topic: str,
        key: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> bool:
        """Persist or deliver one event. Failures must propagate to the caller."""

    @abstractmethod
    async def close(self) -> None:
        """Release owned transport resources."""


class InMemoryEventBus(EventBus):
    """Test-only bus. It is never selected by the runtime factory."""

    def __init__(self) -> None:
        self.published_events: list[dict[str, Any]] = []

    async def publish(
        self, topic: str, key: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> bool:
        self.published_events.append({"topic": topic, "key": key, "payload": payload, "headers": headers or {}})
        return True

    async def close(self) -> None:
        self.published_events.clear()


AsyncSessionFactory = Callable[[], AsyncSession]


class PostgresOutboxEventBus(EventBus):
    """Transactional outbox backed by DB-56 ``integration.outbox_events``."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        if session_factory is None:
            raise EventBusConfigurationError("Postgres outbox requires an async PostgreSQL session factory")
        self._session_factory = session_factory

    @staticmethod
    def _parse_partition_key(key: str) -> tuple[str, str]:
        try:
            tenant_id, workspace_id = key.split(":", 1)
            return str(UUID(tenant_id)), str(UUID(workspace_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("Outbox partition key must be '<tenant UUID>:<workspace UUID>'") from exc

    @staticmethod
    def _aggregate_id(payload: dict[str, Any]) -> str:
        value = payload.get("aggregate_id", payload.get("aggregateId"))
        if not isinstance(value, str):
            raise ValueError("Outbox payload requires aggregate_id")
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise ValueError("Outbox aggregate_id must be a UUID") from exc

    async def publish(
        self, topic: str, key: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> bool:
        tenant_id, workspace_id = self._parse_partition_key(key)
        aggregate_id = self._aggregate_id(payload)
        event_type = payload.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Outbox payload requires a non-empty type")
        envelope = {
            "id": str(payload.get("id") or uuid4()),
            "topic": topic,
            "partition_key": key,
            "headers": headers or {},
            "data": payload,
        }
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": tenant_id},
                )
                await session.execute(
                    text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
                    {"workspace_id": workspace_id},
                )
                await session.execute(
                    text("""
                        INSERT INTO integration.outbox_events
                            (tenant_id, workspace_id, aggregate_type, aggregate_id, event_type, payload, state)
                        VALUES
                            (CAST(:tenant_id AS uuid), CAST(:workspace_id AS uuid), :aggregate_type,
                             CAST(:aggregate_id AS uuid), :event_type, CAST(:payload AS jsonb), 'pending')
                    """),
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "aggregate_type": topic,
                        "aggregate_id": aggregate_id,
                        "event_type": event_type,
                        "payload": json.dumps(envelope, separators=(",", ":")),
                    },
                )
        return True

    async def close(self) -> None:
        return None


class KafkaEventBus(EventBus):
    """Kafka transport. It uses a real producer or fails; it never simulates delivery."""

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        topic_prefix: str | None = None,
        fallback_bus: EventBus | None = None,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers or os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")
        self.topic_prefix = topic_prefix or os.getenv("KAFKA_TOPIC_PREFIX", "")
        self.fallback_bus = fallback_bus
        self._producer: Any | None = None

    async def _producer_or_raise(self) -> Any:
        if self._producer is not None:
            return self._producer
        if not self.bootstrap_servers:
            raise EventBusConfigurationError("KAFKA_BOOTSTRAP_SERVERS is required for Kafka transport")
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError as exc:
            raise EventBusConfigurationError("Kafka transport requires the aiokafka package") from exc
        producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap_servers)
        await producer.start()
        self._producer = producer
        return producer

    async def publish(
        self, topic: str, key: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> bool:
        PostgresOutboxEventBus._parse_partition_key(key)
        full_topic = f"{self.topic_prefix}.{topic}" if self.topic_prefix else topic
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            producer = await self._producer_or_raise()
            await producer.send_and_wait(full_topic, value=body, key=key.encode("utf-8"))
            return True
        except Exception:
            if self.fallback_bus is None:
                raise
            return await self.fallback_bus.publish(topic, key, payload, headers)

    async def close(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None


def get_event_bus(session_factory: AsyncSessionFactory | None = None) -> EventBus:
    """Build a durable bus. In-memory mode is intentionally unavailable in runtime."""
    bus_type = os.getenv("EVENT_BUS_TYPE", "postgres_outbox").lower()
    if bus_type == "postgres_outbox":
        if session_factory is None:
            raise EventBusConfigurationError("Runtime must provide a PostgreSQL session factory")
        return PostgresOutboxEventBus(session_factory)
    if bus_type == "kafka":
        fallback = PostgresOutboxEventBus(session_factory) if session_factory is not None else None
        return KafkaEventBus(fallback_bus=fallback)
    raise EventBusConfigurationError("EVENT_BUS_TYPE must be 'postgres_outbox' or 'kafka'")
