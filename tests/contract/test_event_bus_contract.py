"""Configuration guards for durable event publication."""

import pytest

from platform_common.event_bus import EventBusConfigurationError, PostgresOutboxEventBus, get_event_bus


def test_postgres_outbox_requires_session_factory():
    with pytest.raises(EventBusConfigurationError):
        PostgresOutboxEventBus(None)


def test_runtime_factory_rejects_in_memory_mode(monkeypatch):
    monkeypatch.setenv("EVENT_BUS_TYPE", "in_memory")
    with pytest.raises(EventBusConfigurationError):
        get_event_bus()
