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
from datetime import datetime
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
    def _aggregate_id(payload: dict[str, Any], explicit_aggregate_id: str | UUID | None = None) -> str:
        if explicit_aggregate_id is not None:
            try:
                return str(UUID(str(explicit_aggregate_id)))
            except ValueError as exc:
                raise ValueError("Outbox aggregate_id must be a UUID") from exc
        value = payload.get("aggregate_id", payload.get("aggregateId"))
        if not isinstance(value, str):
            raise TypeError("Outbox payload requires string aggregate_id")
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise ValueError("Outbox aggregate_id must be a UUID") from exc

    @classmethod
    def _prepare_outbox_entry(
        cls,
        topic: str,
        key: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        *,
        aggregate_type: str | None = None,
        aggregate_id: str | UUID | None = None,
        event_id: str | UUID | None = None,
    ) -> tuple[str, str, str, str, str, str, dict[str, Any]]:
        tenant_id, workspace_id = cls._parse_partition_key(key)
        resolved_aggregate_id = cls._aggregate_id(payload, aggregate_id)
        event_type = payload.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Outbox payload requires a non-empty type")
        resolved_event_id = str(UUID(str(event_id))) if event_id is not None else str(payload.get("id") or uuid4())
        resolved_aggregate_type = aggregate_type or topic
        envelope = {
            "id": resolved_event_id,
            "topic": topic,
            "partition_key": key,
            "headers": headers or {},
            "data": payload,
        }
        return (
            resolved_event_id,
            tenant_id,
            workspace_id,
            resolved_aggregate_type,
            resolved_aggregate_id,
            event_type,
            envelope,
        )

    @staticmethod
    async def _insert_outbox_row(
        session: AsyncSession,
        *,
        event_id: str,
        tenant_id: str,
        workspace_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        envelope: dict[str, Any],
        available_at: datetime | None = None,
        set_rls: bool = True,
    ) -> str:
        if set_rls:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
                {"workspace_id": workspace_id},
            )
        if available_at is not None:
            await session.execute(
                text("""
                    INSERT INTO integration.outbox_events
                        (tenant_id, workspace_id, id, aggregate_type, aggregate_id, event_type, payload, state, available_at)
                    VALUES
                        (CAST(:tenant_id AS uuid), CAST(:workspace_id AS uuid), CAST(:id AS uuid),
                         :aggregate_type, CAST(:aggregate_id AS uuid), :event_type, CAST(:payload AS jsonb),
                         'pending', :available_at)
                """),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "id": event_id,
                    "aggregate_type": aggregate_type,
                    "aggregate_id": aggregate_id,
                    "event_type": event_type,
                    "payload": json.dumps(envelope, separators=(",", ":")),
                    "available_at": available_at,
                },
            )
        else:
            await session.execute(
                text("""
                    INSERT INTO integration.outbox_events
                        (tenant_id, workspace_id, id, aggregate_type, aggregate_id, event_type, payload, state)
                    VALUES
                        (CAST(:tenant_id AS uuid), CAST(:workspace_id AS uuid), CAST(:id AS uuid),
                         :aggregate_type, CAST(:aggregate_id AS uuid), :event_type, CAST(:payload AS jsonb),
                         'pending')
                """),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "id": event_id,
                    "aggregate_type": aggregate_type,
                    "aggregate_id": aggregate_id,
                    "event_type": event_type,
                    "payload": json.dumps(envelope, separators=(",", ":")),
                },
            )
        return event_id

    async def publish_in_transaction(
        self,
        session: AsyncSession,
        topic: str,
        key: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        *,
        aggregate_type: str | None = None,
        aggregate_id: str | UUID | None = None,
        event_id: str | UUID | None = None,
        available_at: datetime | None = None,
        set_rls: bool = False,
    ) -> str:
        """Persist an outbox event within an active caller transaction."""
        prepared = self._prepare_outbox_entry(
            topic,
            key,
            payload,
            headers,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_id=event_id,
        )
        (
            resolved_event_id,
            tenant_id,
            workspace_id,
            resolved_aggregate_type,
            resolved_aggregate_id,
            event_type,
            envelope,
        ) = prepared
        return await self._insert_outbox_row(
            session,
            event_id=resolved_event_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            aggregate_type=resolved_aggregate_type,
            aggregate_id=resolved_aggregate_id,
            event_type=event_type,
            envelope=envelope,
            available_at=available_at,
            set_rls=set_rls,
        )

    async def publish(
        self, topic: str, key: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> bool:
        prepared = self._prepare_outbox_entry(topic, key, payload, headers)
        (
            event_id,
            tenant_id,
            workspace_id,
            aggregate_type,
            aggregate_id,
            event_type,
            envelope,
        ) = prepared
        async with self._session_factory() as session, session.begin():
            await self._insert_outbox_row(
                session,
                event_id=event_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                envelope=envelope,
                set_rls=True,
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
