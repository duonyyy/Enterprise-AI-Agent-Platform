"""Domain errors for Agent draft operations."""

from __future__ import annotations


class AgentDomainError(Exception):
    """Base domain error for agent lifecycle operations."""

    def __init__(self, message: str, code: str = "agent_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class AgentNotFoundError(AgentDomainError):
    """Raised when an agent does not exist in the authorized workspace."""

    def __init__(
        self, message: str = "Agent not found in workspace", code: str = "agent_not_found"
    ) -> None:
        super().__init__(message, code=code)


class AgentSlugConflictError(AgentDomainError):
    """Raised when an agent slug already exists within the target workspace."""

    def __init__(self, message: str = "Agent slug conflict", code: str = "slug_conflict") -> None:
        super().__init__(message, code=code)


class AgentArchivedError(AgentDomainError):
    """Raised when attempting to modify an archived agent draft."""

    def __init__(
        self, message: str = "Archived agent draft cannot be modified", code: str = "conflict"
    ) -> None:
        super().__init__(message, code=code)


class AgentStaleRevisionError(AgentDomainError):
    """Raised when the expected If-Match revision does not match committed state."""

    def __init__(
        self, message: str = "Draft revision is stale", code: str = "stale_revision"
    ) -> None:
        super().__init__(message, code=code)


class AgentValidationError(AgentDomainError):
    """Raised when an AgentSpec or request envelope is structurally invalid."""

    def __init__(self, message: str, code: str = "invalid_spec") -> None:
        super().__init__(message, code=code)


class AgentAccessDeniedError(AgentDomainError):
    """Raised when the actor lacks required live workspace authority."""

    def __init__(
        self, message: str = "Workspace permission denied", code: str = "access_denied"
    ) -> None:
        super().__init__(message, code=code)


class AgentDatabaseUnavailableError(AgentDomainError):
    """Raised when the underlying database or transaction boundary fails."""

    def __init__(
        self, message: str = "Database temporarily unavailable", code: str = "service_unavailable"
    ) -> None:
        super().__init__(message, code=code)


class AgentCapabilityUnavailableError(AgentDomainError):
    """Raised when a requested capability (e.g. publish in 04-A) is not yet available."""

    def __init__(
        self,
        message: str = "Agent publish capability is disabled until STEP-04C",
        code: str = "capability_unavailable",
    ) -> None:
        super().__init__(message, code=code)


class AgentIdempotencyConflictError(AgentDomainError):
    """Raised when an idempotency key is re-used with a different payload or actor (HTTP 409)."""

    def __init__(
        self,
        message: str = "Idempotency key collision with different request payload or actor",
        code: str = "idempotency_conflict",
    ) -> None:
        super().__init__(message, code=code)
