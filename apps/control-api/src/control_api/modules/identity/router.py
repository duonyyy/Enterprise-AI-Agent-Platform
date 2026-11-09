"""FastAPI Router for Authentication and Session endpoints (SPEC-AUTH-001 / Step 02)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from control_api.core.auth import verify_csrf_or_origin
from control_api.core.dependencies import CurrentAuthenticatedUser, DBSession
from control_api.core.settings import settings
from control_api.modules.identity.crypto import generate_secure_token
from control_api.modules.identity.dto import (
    CallbackResponse,
    CurrentUserResponse,
    LoginInitiateResponse,
    TokenRequestDTO,
    TokenResponseDTO,
)
from control_api.modules.identity.oidc import oidc_client
from control_api.modules.identity.service import identity_service
from control_api.modules.identity.session import (
    clear_session_cookie,
    get_clear_session_cookie_headers,
    session_manager,
    set_session_cookie,
)
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.get("/login", response_model=LoginInitiateResponse)
async def login(
    response: Response = None,  # type: ignore[assignment]
    return_to: str | None = Query(default=None),
    redirect: bool = Query(default=False),
) -> Any:
    """Initiate OIDC Authorization Code Flow with PKCE S256 and browser correlation (Finding R02)."""
    correlation_id = generate_secure_token(32)
    auth_url, state = oidc_client.initiate_login(return_to=return_to, browser_correlation=correlation_id)

    if redirect:
        redirect_res = RedirectResponse(url=auth_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        redirect_res.set_cookie(
            key=settings.oidc_correlation_cookie_name,
            value=correlation_id,
            max_age=600,
            httponly=True,
            secure=settings.oidc_correlation_cookie_secure,
            samesite=settings.oidc_correlation_cookie_same_site,  # type: ignore[arg-type]
            path="/v1/auth/callback",
        )
        return redirect_res

    if response is not None:
        response.set_cookie(
            key=settings.oidc_correlation_cookie_name,
            value=correlation_id,
            max_age=600,
            httponly=True,
            secure=settings.oidc_correlation_cookie_secure,
            samesite=settings.oidc_correlation_cookie_same_site,  # type: ignore[arg-type]
            path="/v1/auth/callback",
        )
    return LoginInitiateResponse(authorization_url=auth_url, state=state)


@router.get("/callback", response_model=CallbackResponse)
async def callback(
    state: str = Query(...),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    request: Request = None,  # type: ignore[assignment]
    response: Response = None,  # type: ignore[assignment]
    session: DBSession = None,  # type: ignore[assignment]
) -> Any:
    """Handle callback from IdP with authorization code, verify browser correlation, and establish session."""
    def _clean_corr(res: Response | None) -> None:
        if res is not None:
            res.delete_cookie(
                key=settings.oidc_correlation_cookie_name,
                path="/v1/auth/callback",
                httponly=True,
                secure=settings.oidc_correlation_cookie_secure,
                samesite=settings.oidc_correlation_cookie_same_site,  # type: ignore[arg-type]
            )

    if error:
        oidc_client.store.consume(state)
        accept = request.headers.get("accept", "") if request else ""
        if "text/html" in accept:
            redirect_url = f"/login?error={quote_plus(error)}"
            if error_description:
                redirect_url += f"&error_description={quote_plus(error_description)}"
            redirect_res = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
            _clean_corr(redirect_res)
            return redirect_res
        _clean_corr(response)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Identity Provider returned error: {error}",
        )

    if not code:
        _clean_corr(response)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code from Identity Provider",
        )

    # Browser correlation verification (RFC 9700 §2.1.1 / Finding R02)
    corr_cookie = request.cookies.get(settings.oidc_correlation_cookie_name) if request else None

    payload, tx = await oidc_client.handle_callback(
        code=code,
        state=state,
        browser_correlation=corr_cookie,
    )

    issuer = str(payload.get("iss", settings.oidc_issuer))
    subject = str(payload["sub"])
    display_name = payload.get("name") or payload.get("preferred_username")
    email = payload.get("email")

    user = await identity_service.get_or_provision_user(
        session=session,
        issuer=issuer,
        subject=subject,
        display_name=str(display_name) if display_name else None,
        email=str(email) if email else None,
    )

    user_id = str(user["id"])
    session_data, session_token = session_manager.create_session(
        user_id=user_id,
        issuer=issuer,
        subject=subject,
    )

    # Check client's Accept header: if HTML or browser navigation, 302 redirect with cookie
    accept = request.headers.get("accept", "") if request else ""
    if "text/html" in accept:
        redirect_res = RedirectResponse(url=tx.return_to, status_code=status.HTTP_302_FOUND)
        set_session_cookie(
            redirect_res,
            session_token,
            absolute_expires_at=session_data.absolute_expires_at,
        )
        _clean_corr(redirect_res)
        return redirect_res

    # Otherwise set cookie on JSON response
    set_session_cookie(
        response,
        session_token,
        absolute_expires_at=session_data.absolute_expires_at,
    )
    _clean_corr(response)
    return CallbackResponse(
        user_id=user_id,
        issuer=issuer,
        subject=subject,
        display_name=user.get("display_name"),
        email_normalized=user.get("email_normalized"),
        redirect_url=tx.return_to,
    )


@router.get("/me", response_model=CurrentUserResponse)
async def get_current_user(
    auth_user: CurrentAuthenticatedUser,
    session: DBSession,
) -> CurrentUserResponse:
    """Retrieve current authenticated user profile and list of active allowed workspaces."""
    user = await identity_service.get_user_by_id(session, auth_user.user_id)
    display_name = user.get("display_name") if user else None
    email_normalized = user.get("email_normalized") if user else None

    workspaces = await identity_service.get_user_workspaces(session, auth_user.user_id)

    return CurrentUserResponse(
        user_id=auth_user.user_id,
        issuer=auth_user.issuer,
        subject=auth_user.subject,
        display_name=display_name,
        email_normalized=email_normalized,
        workspaces=workspaces,
    )


@router.post("/refresh")
async def refresh_session(
    request: Request,
    response: Response,
    auth_user: CurrentAuthenticatedUser,
) -> dict[str, str]:
    """Atomically rotate active session within absolute lifetime window and issue a new session cookie."""
    verify_csrf_or_origin(request)

    if not auth_user.session_data:
        # Scoped tokens cannot be refreshed via session endpoint (Finding R05)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Active application session required to refresh session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    new_session, new_token = session_manager.refresh_session(auth_user.session_data)
    set_session_cookie(
        response,
        new_token,
        absolute_expires_at=new_session.absolute_expires_at,
    )
    return {"status": "refreshed"}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
) -> dict[str, str]:
    """Revoke active session token, put session_id on revocation denylist, and clear cookie.

    Idempotent local cleanup for missing, expired, or invalid credentials.
    Fails closed with HTTP 503 and client cookie deletion if revocation store is unavailable.
    """
    verify_csrf_or_origin(request)

    # Read cookie or header
    token = request.cookies.get(settings.session_cookie_name)
    if not token and request.headers.get("authorization", "").startswith("Bearer "):
        token = request.headers["authorization"].removeprefix("Bearer ").strip()

    if token:
        try:
            session_manager.revoke_session_by_token(token)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
                headers = dict(exc.headers) if exc.headers else {}
                headers.update(get_clear_session_cookie_headers())
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Revocation store unavailable; local session cleared but server-side revocation could not be confirmed",
                    headers=headers,
                ) from exc
            raise

    clear_session_cookie(response)
    return {"status": "logged_out"}


@router.post("/token", response_model=TokenResponseDTO)
async def exchange_workspace_token(
    dto: TokenRequestDTO,
    request: Request,
    auth_user: CurrentAuthenticatedUser,
    session: DBSession,
) -> TokenResponseDTO:
    """Exchange active application session for a short-lived workspace scoped API token."""
    verify_csrf_or_origin(request)

    # Confinement (Finding R05):
    # Token exchange strictly requires an active parent application session.
    # Scoped access tokens cannot self-reissue or switch workspaces.
    if not auth_user.session_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Active application session required for token exchange; scoped access tokens cannot be exchanged for new tokens",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await identity_service.issue_workspace_token(
        session=session,
        user_id=auth_user.user_id,
        workspace_id=dto.workspace_id,
        session_data=auth_user.session_data,
    )
