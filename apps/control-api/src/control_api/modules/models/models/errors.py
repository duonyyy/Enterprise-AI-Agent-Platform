"""Domain errors for model registry operations."""

from __future__ import annotations


class ModelConfigDomainError(Exception):
    def __init__(self, message: str, *, code: str = "model_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ModelConfigNotFoundError(ModelConfigDomainError):
    def __init__(self, message: str = "Model configuration not found") -> None:
        super().__init__(message, code="model_not_found")


class ModelConfigConflictError(ModelConfigDomainError):
    def __init__(self, message: str = "Model configuration name already exists in workspace") -> None:
        super().__init__(message, code="model_conflict")


class ModelConfigValidationError(ModelConfigDomainError):
    def __init__(self, message: str, *, code: str = "invalid_model_config") -> None:
        super().__init__(message, code=code)


class ModelAccessDeniedError(ModelConfigDomainError):
    def __init__(self, message: str = "Insufficient permissions to manage models") -> None:
        super().__init__(message, code="model_access_denied")


class ModelDatabaseUnavailableError(ModelConfigDomainError):
    def __init__(self, message: str = "Model database service temporarily unavailable") -> None:
        super().__init__(message, code="database_unavailable")


ModelConfigDatabaseUnavailableError = ModelDatabaseUnavailableError
