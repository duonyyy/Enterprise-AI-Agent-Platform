"""Platform Common v2.0."""

from platform_common.errors import (
    PlatformError,
    TenantIsolationViolation,
    StaleUpdateConflictError
)
from platform_common.principal import AuthenticatedPrincipal
from platform_common.event_bus import (
    EventBus,
    InMemoryEventBus,
    PostgresOutboxEventBus,
    KafkaEventBus,
    get_event_bus
)
from platform_common.audit_ledger import (
    AuditRecord,
    TamperEvidentLedger,
    AuditIntegrityError,
    GENESIS_HASH
)

__all__ = [
    "PlatformError",
    "TenantIsolationViolation",
    "StaleUpdateConflictError",
    "AuthenticatedPrincipal",
    "EventBus",
    "InMemoryEventBus",
    "PostgresOutboxEventBus",
    "KafkaEventBus",
    "get_event_bus",
    "AuditRecord",
    "TamperEvidentLedger",
    "AuditIntegrityError",
    "GENESIS_HASH"
]
