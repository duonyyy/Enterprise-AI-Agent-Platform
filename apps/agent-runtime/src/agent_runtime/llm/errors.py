"""Standard Model Gateway error taxonomy according to SPEC-LLM-001 section 5."""

from __future__ import annotations


class ModelGatewayError(Exception):
    """Base exception for all Model Gateway errors."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class ModelAuthError(ModelGatewayError):
    """Authentication failed against provider or secret resolver."""

    def __init__(self, message: str = "Model authentication failed") -> None:
        super().__init__("MODEL_AUTH_FAILED", message)


class ModelRateLimitedError(ModelGatewayError):
    """Provider rate limit or quota exceeded."""

    def __init__(self, message: str = "Model rate limit exceeded") -> None:
        super().__init__("MODEL_RATE_LIMITED", message)


class ModelTimeoutError(ModelGatewayError):
    """Deadline exceeded before or during model call."""

    def __init__(self, message: str = "Model call timed out") -> None:
        super().__init__("MODEL_TIMEOUT", message)


class ModelUnavailableError(ModelGatewayError):
    """Model or provider is temporarily or permanently unavailable."""

    def __init__(self, message: str = "Model is unavailable") -> None:
        super().__init__("MODEL_UNAVAILABLE", message)


class ModelContextTooLargeError(ModelGatewayError):
    """Requested input exceeds model context allowance."""

    def __init__(self, message: str = "Context exceeds model allowance") -> None:
        super().__init__("MODEL_CONTEXT_TOO_LARGE", message)


class ModelInvalidResponseError(ModelGatewayError):
    """Provider returned malformed, unparseable, or non-finite output."""

    def __init__(self, message: str = "Invalid response from model") -> None:
        super().__init__("MODEL_INVALID_RESPONSE", message)


class ModelPolicyDeniedError(ModelGatewayError):
    """Call denied by IAM, disabled model config, or tenancy policy."""

    def __init__(self, message: str = "Model call denied by policy") -> None:
        super().__init__("MODEL_POLICY_DENIED", message)


class ModelBudgetExceededError(ModelGatewayError):
    """Requested call exceeds workspace budget or remaining cost limit."""

    def __init__(self, message: str = "Model budget exceeded") -> None:
        super().__init__("MODEL_BUDGET_EXCEEDED", message)


class ModelPricingUnavailableError(ModelGatewayError):
    """A monetary budget cannot be admitted without an approved price snapshot."""

    def __init__(self, message: str = "Model pricing is unavailable") -> None:
        super().__init__("MODEL_PRICING_UNAVAILABLE", message)


class ModelBudgetNotConfiguredError(ModelGatewayError):
    """No active workspace budget balance exists for the trusted scope."""

    def __init__(self, message: str = "Workspace model budget is not configured") -> None:
        super().__init__("MODEL_BUDGET_NOT_CONFIGURED", message)


class ModelReservationNotFoundError(ModelGatewayError):
    """The requested reservation or attempt is absent in the trusted scope."""

    def __init__(self, message: str = "Model budget reservation was not found") -> None:
        super().__init__("MODEL_RESERVATION_NOT_FOUND", message)


class IdempotencyConflictError(ModelGatewayError):
    """Operation key reused with conflicting request payload, actor, or scope."""

    def __init__(self, message: str = "Idempotency key collision with conflicting payload") -> None:
        super().__init__("IDEMPOTENCY_CONFLICT", message)


class ReservationExpiredError(ModelGatewayError):
    """Reservation has expired and can no longer be settled."""

    def __init__(self, message: str = "Model budget reservation expired") -> None:
        super().__init__("RESERVATION_EXPIRED", message)


class SettlementConflictError(ModelGatewayError):
    """Invalid state transition or duplicate settlement on reservation."""

    def __init__(self, message: str = "Invalid settlement state transition") -> None:
        super().__init__("SETTLEMENT_CONFLICT", message)


class SecretNotFoundError(ModelGatewayError):
    """Secret reference could not be resolved in the target scope."""

    def __init__(self, message: str = "Secret reference not found") -> None:
        super().__init__("SECRET_NOT_FOUND", message)


class SecretAccessDeniedError(ModelGatewayError):
    """Access to secret reference denied across tenant or service scope."""

    def __init__(self, message: str = "Access to secret reference denied") -> None:
        super().__init__("SECRET_ACCESS_DENIED", message)
