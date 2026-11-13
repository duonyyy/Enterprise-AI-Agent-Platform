"""Shared HTTP cookie helpers for invitation handoff state across Control API routers."""

from __future__ import annotations

from fastapi import Response

from control_api.core.settings import settings


def set_invitation_handoff_cookie(
    response: Response,
    browser_token: str,
    *,
    max_age: int | None = None,
) -> None:
    """Set hardened HttpOnly browser-binding cookie for invitation handoffs (ADR-020)."""
    effective_max_age = (
        max(0, min(max_age, settings.invitation_handoff_ttl_seconds))
        if max_age is not None
        else settings.invitation_handoff_ttl_seconds
    )
    response.set_cookie(
        key=settings.invitation_handoff_cookie_name,
        value=browser_token,
        max_age=effective_max_age,
        httponly=True,
        secure=settings.invitation_handoff_cookie_secure,
        samesite=settings.invitation_handoff_cookie_same_site,  # type: ignore[arg-type]
        path=settings.invitation_handoff_cookie_path,
    )


def clear_invitation_handoff_cookie(response: Response | None) -> None:
    """Clear browser-binding handoff cookie without touching session cookies."""
    if response is not None:
        response.delete_cookie(
            key=settings.invitation_handoff_cookie_name,
            path=settings.invitation_handoff_cookie_path,
            httponly=True,
            secure=settings.invitation_handoff_cookie_secure,
            samesite=settings.invitation_handoff_cookie_same_site,  # type: ignore[arg-type]
        )


__all__ = [
    "clear_invitation_handoff_cookie",
    "set_invitation_handoff_cookie",
]
