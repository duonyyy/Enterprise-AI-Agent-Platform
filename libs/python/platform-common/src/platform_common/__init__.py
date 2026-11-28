"""Platform Common v2.0."""

from platform_common.audit_ledger import (
    GENESIS_HASH,
    AuditIntegrityError,
    AuditRecord,
    TamperEvidentLedger,
)
from platform_common.errors import PlatformError, StaleUpdateConflictError, TenantIsolationViolation
from platform_common.event_bus import (
    EventBus,
    InMemoryEventBus,
    KafkaEventBus,
    PostgresOutboxEventBus,
    get_event_bus,
)
from platform_common.execution import (
    AsyncRunExecutor,
    ExecutionCommand,
    ExecutionDisposition,
    ExecutionDispositionType,
    LeaseIdentity,
)
from platform_common.principal import AuthenticatedPrincipal
from platform_common.rls import set_rls_context

__all__ = [
    "GENESIS_HASH",
    "AsyncRunExecutor",
    "AuditIntegrityError",
    "AuditRecord",
    "AuthenticatedPrincipal",
    "EventBus",
    "ExecutionCommand",
    "ExecutionDisposition",
    "ExecutionDispositionType",
    "InMemoryEventBus",
    "KafkaEventBus",
    "LeaseIdentity",
    "PlatformError",
    "PostgresOutboxEventBus",
    "StaleUpdateConflictError",
    "TamperEvidentLedger",
    "TenantIsolationViolation",
    "get_event_bus",
    "set_rls_context",
]

