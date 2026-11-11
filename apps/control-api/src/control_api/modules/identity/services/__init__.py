"""Services package for identity module."""

from typing import Any


def __getattr__(name: str) -> Any:
    if name in ("IdentityService", "WorkspaceAccessDeniedError", "identity_service"):
        from control_api.modules.identity.services import auth_service

        return getattr(auth_service, name)
    if name in ("SessionManager", "session_manager"):
        from control_api.modules.identity.services import session_service

        return getattr(session_service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "IdentityService",
    "SessionManager",
    "WorkspaceAccessDeniedError",
    "identity_service",
    "session_manager",
]
