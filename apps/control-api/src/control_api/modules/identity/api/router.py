"""FastAPI Router for Authentication and Session endpoints (SPEC-AUTH-001 / Step 02)."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import quote_plus
from uuid import UUID

from control_api.core.auth import verify_csrf_or_origin
from control_api.core.dependencies import CurrentAuthenticatedUser, DBSession
from control_api.core.settings import settings
from control_api.modules.identity.api.dependencies import (
    clear_correlation_cookie,
    clear_invitation_handoff_cookie,
    clear_session_cookie,
    set_correlation_cookie,
    set_session_cookie,
)
from control_api.modules.identity.dto import (
    CallbackResponse,
    CurrentUserResponse,
    LoginInitiateResponse,
    TokenRequestDTO,
    TokenResponseDTO,
)
from control_api.modules.identity.models.errors import RepositoryUnavailableError
from control_api.modules.identity.models.session import VerifiedIdentityEvidence
from control_api.modules.identity.providers.oidc_client import oidc_client
from control_api.modules.identity.security.crypto import generate_secure_token
from control_api.modules.identity.services.auth_service import identity_service
from control_api.modules.identity.services.session_service import session_manager
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from platform_common.invitations import normalize_invitation_email

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _extract_id_token_evidence(
    *,
    payload: dict[str, Any],
    issuer: str,
    subject: str,
    user_id: str,
    observed_at: int,
) -> VerifiedIdentityEvidence | None:
    """Build strict VerifiedIdentityEvidence from OIDC ID token claims when iat is valid."""
    raw_iat = payload.get("iat")
    if type(raw_iat) is int:
        id_token_iat = raw_iat
    elif isinstance(raw_iat, float) and raw_iat.is_integer():
        id_token_iat = int(raw_iat)
    else:
        return None
    if id_token_iat <= 0:
        return None

    raw_email = payload.get("email")
    normalized_email = ""
    if isinstance(raw_email, str) and raw_email.strip():
        try:
            normalized_email = normalize_invitation_email(raw_email)
        except ValueError:
            normalized_email = ""

    raw_verified = payload.get("email_verified")
    email_verified = raw_verified is True and bool(normalized_email)

    return VerifiedIdentityEvidence(
        issuer=issuer,
        subject=subject,
        user_id=user_id,
        email_normalized=normalized_email,
        email_verified=email_verified,
        id_token_issued_at=id_token_iat,
        observed_at=observed_at,
    )


@router.get("/login", response_model=LoginInitiateResponse)
async def login(
    request: Request = None,  # type: ignore[assignment]
    response: Response = None,  # type: ignore[assignment]
    return_to: str | None = Query(default=None),
    redirect: bool = Query(default=False),
    invitation_id: UUID | None = Query(default=None),
    reauth: bool = Query(default=False),
) -> Any:
    """Initiate OIDC Authorization Code Flow with PKCE S256 and browser correlation (Finding R02)."""
    correlation_id = generate_secure_token(32)
    handoff_cookie = (
        request.cookies.get(settings.invitation_handoff_cookie_name)
        if request is not None
        else None
    )
    handoff_digest = (
        _sha256_hex(handoff_cookie)
        if invitation_id is not None and isinstance(handoff_cookie, str) and handoff_cookie
        else None
    )
    auth_url, state = oidc_client.initiate_login(
        return_to=return_to,
        browser_correlation=correlation_id,
        invitation_id=str(invitation_id) if invitation_id is not None else None,
        handoff_binding_digest=handoff_digest,
        reauth=reauth,
    )

    if redirect:
        redirect_res = RedirectResponse(
            url=auth_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT
        )
        set_correlation_cookie(redirect_res, correlation_id)
        return redirect_res

    if response is not None:
        set_correlation_cookie(response, correlation_id)
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
    if error:
        oidc_client.consume_transaction(state)
        accept = request.headers.get("accept", "") if request else ""
        if "text/html" in accept:
            redirect_url = f"/login?error={quote_plus(error)}"
            if error_description:
                redirect_url += f"&error_description={quote_plus(error_description)}"
            redirect_res = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
            clear_correlation_cookie(redirect_res)
            return redirect_res
        clear_correlation_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Identity Provider returned error: {error}",
        )

    if not code:
        clear_correlation_cookie(response)
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

    if tx.pending_handoff_binding_digest:
        handoff_cookie = (
            request.cookies.get(settings.invitation_handoff_cookie_name) if request else None
        )
        cookie_matches = (
            isinstance(handoff_cookie, str)
            and bool(handoff_cookie)
            and hmac.compare_digest(_sha256_hex(handoff_cookie), tx.pending_handoff_binding_digest)
        )
        if not cookie_matches:
            accept_hdr = request.headers.get("accept", "") if request else ""
            if "text/html" in accept_hdr and tx.pending_invitation_id:
                mismatch_res = RedirectResponse(
                    url=f"/invitations/{tx.pending_invitation_id}/accept?error=handoff_binding_mismatch",
                    status_code=status.HTTP_302_FOUND,
                )
                clear_correlation_cookie(mismatch_res)
                return mismatch_res
            clear_correlation_cookie(response)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invitation handoff browser binding mismatch",
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
    observed_at = int(time.time())
    identity_evidence = _extract_id_token_evidence(
        payload=payload,
        issuer=issuer,
        subject=subject,
        user_id=user_id,
        observed_at=observed_at,
    )
    session_data, session_token = session_manager.create_session(
        user_id=user_id,
        issuer=issuer,
        subject=subject,
        now=observed_at,
        identity_evidence=identity_evidence,
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
        clear_correlation_cookie(redirect_res)
        return redirect_res

    # Otherwise set cookie on JSON response
    set_session_cookie(
        response,
        session_token,
        absolute_expires_at=session_data.absolute_expires_at,
    )
    clear_correlation_cookie(response)
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
) -> Any:
    """Revoke active session token, put session_id on revocation denylist, and clear cookies.

    Idempotent local cleanup for missing, expired, or invalid credentials.
    Fails closed with HTTP 503 and client cookie deletion (session + handoff)
    if revocation store is unavailable, keeping multiple Set-Cookie headers intact.
    """
    verify_csrf_or_origin(request)

    # Read cookie or header
    token = request.cookies.get(settings.session_cookie_name)
    if not token and request.headers.get("authorization", "").startswith("Bearer "):
        token = request.headers["authorization"].removeprefix("Bearer ").strip()

    if token:
        try:
            session_manager.revoke_session_by_token(token)
        except (HTTPException, RepositoryUnavailableError) as exc:
            if (
                isinstance(exc, HTTPException)
                and exc.status_code != status.HTTP_503_SERVICE_UNAVAILABLE
            ):
                raise
            err_response = JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "detail": "Revocation store unavailable; local session cleared but server-side revocation could not be confirmed"
                },
                headers=dict(exc.headers) if isinstance(exc, HTTPException) and exc.headers else None,
            )
            clear_session_cookie(err_response)
            clear_invitation_handoff_cookie(err_response)
            return err_response

    clear_session_cookie(response)
    clear_invitation_handoff_cookie(response)
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
