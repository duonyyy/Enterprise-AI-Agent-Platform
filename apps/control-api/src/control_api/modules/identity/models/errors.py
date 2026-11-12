"""Domain and repository exceptions for the Identity module (SPEC-AUTH-001).

Pure Python exception hierarchy free of Web/HTTP framework dependencies (CT-038, AR-008).
Upper HTTP layers (FastAPI global exception handlers, routers, or HTTP service facades)
map these errors to HTTP status codes (e.g., 503 Service Unavailable, 401 Unauthorized).
"""

from __future__ import annotations

from control_api.core.errors import RepositoryUnavailableError


class IdentityError(Exception):
    """Base domain exception for identity and authentication failures."""


class AuthorizationDatabaseUnavailableError(RepositoryUnavailableError, IdentityError):
    """Raised when PostgreSQL authorization lookup or projection fails."""


class RevocationStoreUnavailableError(RepositoryUnavailableError, IdentityError):
    """Raised when distributed Redis session/revocation storage fails."""


class AuthTransactionStoreUnavailableError(RepositoryUnavailableError, IdentityError):
    """Raised when distributed Redis OIDC transaction storage fails."""


class SessionExpiredError(IdentityError):
    """Raised when a session credential has passed its lifetime boundary."""


__all__ = [
    "AuthTransactionStoreUnavailableError",
    "AuthorizationDatabaseUnavailableError",
    "IdentityError",
    "RepositoryUnavailableError",
    "RevocationStoreUnavailableError",
    "SessionExpiredError",
]
