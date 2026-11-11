"""FastAPI HTTP dependencies and cookie helpers for Identity API (SPEC-AUTH-001)."""

from __future__ import annotations

import time

from control_api.core.settings import settings
from fastapi import Response


def set_session_cookie(
    response: Response,
    session_token: str,
    *,
    max_age: int | None = None,
    absolute_expires_at: int | None = None,
    now: int | None = None,
) -> None:
    """Set hardened HTTP-Only session cookie on the response.

    Max-Age is bounded by the remaining absolute lifetime of the session so that
    refresh never extends the browser cookie by another full absolute lifetime.
    """
    if max_age is not None:
        effective_max_age = max(0, min(max_age, settings.session_absolute_timeout_seconds))
    elif absolute_expires_at is not None:
        current = now if now is not None else int(time.time())
        remaining = max(0, absolute_expires_at - current)
        effective_max_age = min(remaining, settings.session_absolute_timeout_seconds)
    else:
        effective_max_age = settings.session_absolute_timeout_seconds

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=effective_max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_same_site,  # type: ignore[arg-type]
        path=settings.session_cookie_path,
    )


def clear_session_cookie(response: Response) -> None:
    """Clear session cookie from the client."""
    response.delete_cookie(
        key=settings.session_cookie_name,
        path=settings.session_cookie_path,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_same_site,  # type: ignore[arg-type]
    )


def get_clear_session_cookie_headers() -> dict[str, str]:
    """Generate Set-Cookie deletion headers for clearing the session cookie on exception responses."""
    temp_resp = Response()
    clear_session_cookie(temp_resp)
    cookie_val = temp_resp.headers.get("set-cookie")
    return {"Set-Cookie": cookie_val} if cookie_val else {}


def set_correlation_cookie(response: Response, correlation_id: str) -> None:
    """Set browser correlation cookie for OIDC state protection."""
    response.set_cookie(
        key=settings.oidc_correlation_cookie_name,
        value=correlation_id,
        max_age=600,
        httponly=True,
        secure=settings.oidc_correlation_cookie_secure,
        samesite=settings.oidc_correlation_cookie_same_site,  # type: ignore[arg-type]
        path="/v1/auth/callback",
    )


def clear_correlation_cookie(response: Response | None) -> None:
    """Clear browser correlation cookie."""
    if response is not None:
        response.delete_cookie(
            key=settings.oidc_correlation_cookie_name,
            path="/v1/auth/callback",
            httponly=True,
            secure=settings.oidc_correlation_cookie_secure,
            samesite=settings.oidc_correlation_cookie_same_site,  # type: ignore[arg-type]
        )
