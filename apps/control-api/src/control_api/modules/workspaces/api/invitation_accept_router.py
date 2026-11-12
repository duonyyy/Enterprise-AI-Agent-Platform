"""FastAPI Router for global browser-bound invitation handoff, preview, and acceptance (ADR-020)."""

from __future__ import annotations

import ipaddress
import json
import time
from datetime import datetime
from uuid import UUID, uuid4

from control_api.core.auth import (
    get_current_authenticated_user,
    verify_invitation_csrf_or_origin,
)
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.modules.identity.api.dependencies import set_invitation_handoff_cookie
from control_api.modules.identity.models.errors import RepositoryUnavailableError
from control_api.modules.workspaces.dto import (
    InvitationAcceptResponseDTO,
    InvitationHandoffRequestDTO,
    InvitationHandoffResponseDTO,
    InvitationPreviewResponseDTO,
)
from control_api.modules.workspaces.models.errors import (
    InvitationConfigurationError,
    InvitationConflictError,
    InvitationForbiddenError,
    InvitationGoneError,
    InvitationNotFoundError,
    InvitationRateLimitError,
    WorkspaceDatabaseUnavailableError,
)
from control_api.modules.workspaces.services.invitation_acceptance_service import (
    InvitationAcceptanceService,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/v1/invitations", tags=["invitations"])
acceptance_service = InvitationAcceptanceService()


def _apply_no_store_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"


def _parse_invitation_uuid(invitation_id: str) -> UUID:
    try:
        return UUID(invitation_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_invitation_id",
                "message": "invitation_id must be a valid UUID",
            },
        ) from exc


def _extract_trusted_client_ip(request: Request) -> str:
    peer_host = request.client.host if request.client and request.client.host else "unknown"
    trusted_cidrs = settings.trusted_proxy_cidrs
    if not trusted_cidrs or peer_host == "unknown":
        return peer_host
    try:
        peer_ip = ipaddress.ip_address(peer_host)
        is_trusted_proxy = any(
            peer_ip in ipaddress.ip_network(cidr, strict=False) for cidr in trusted_cidrs
        )
    except ValueError:
        return peer_host
    if not is_trusted_proxy:
        return peer_host

    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    for candidate in reversed(parts):
        try:
            cand_ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not any(cand_ip in ipaddress.ip_network(cidr, strict=False) for cidr in trusted_cidrs):
            return str(cand_ip)
    return peer_host


async def _enforce_empty_json_body(request: Request) -> None:
    raw = await request.body()
    stripped = raw.strip()
    if not stripped:
        return
    try:
        parsed = json.loads(stripped.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_request_body",
                "message": "Request body must be empty",
            },
        ) from exc
    if parsed != {}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "unexpected_request_body",
                "message": "Extra request body fields are not permitted",
            },
        )


def _translate_accept_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InvitationNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, InvitationForbiddenError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, InvitationGoneError):
        return HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, InvitationConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, InvitationRateLimitError):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": exc.code, "message": exc.message},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    if isinstance(
        exc,
        (
            InvitationConfigurationError,
            WorkspaceDatabaseUnavailableError,
            RepositoryUnavailableError,
        ),
    ):
        msg = exc.message if hasattr(exc, "message") else str(exc)
        code = exc.code if hasattr(exc, "code") else "invitation_service_unavailable"
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": code, "message": msg},
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "invitation_internal_error", "message": "Unexpected invitation error"},
    )


@router.post(
    "/{invitation_id}/handoff",
    response_model=InvitationHandoffResponseDTO,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation_handoff(
    invitation_id: str,
    payload: InvitationHandoffRequestDTO,
    request: Request,
    response: Response,
) -> InvitationHandoffResponseDTO:
    """Persist encrypted browser-bound handoff from email link fragment before OIDC/preview."""
    _apply_no_store_headers(response)
    verify_invitation_csrf_or_origin(request)
    parsed_id = _parse_invitation_uuid(invitation_id)
    existing_cookie = request.cookies.get(settings.invitation_handoff_cookie_name)
    client_ip = _extract_trusted_client_ip(request)
    try:
        dto, browser_token = acceptance_service.create_handoff(
            invitation_id=parsed_id,
            raw_token=payload.token,
            existing_browser_cookie=existing_cookie,
            client_ip=client_ip,
        )
    except Exception as exc:
        raise _translate_accept_error(exc) from exc

    remaining_max_age = max(
        1,
        min(
            settings.invitation_handoff_ttl_seconds,
            int(datetime.fromisoformat(dto.expires_at).timestamp()) - int(time.time()),
        ),
    )
    set_invitation_handoff_cookie(response, browser_token, max_age=remaining_max_age)
    return dto


@router.post(
    "/{invitation_id}/preview",
    response_model=InvitationPreviewResponseDTO,
)
async def preview_invitation(
    invitation_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> InvitationPreviewResponseDTO:
    """Preview invitation metadata after verifying application session, fresh OIDC evidence, and handoff."""
    _apply_no_store_headers(response)
    verify_invitation_csrf_or_origin(request)
    await _enforce_empty_json_body(request)
    parsed_id = _parse_invitation_uuid(invitation_id)
    auth_user = await get_current_authenticated_user(
        request, authorization=request.headers.get("authorization")
    )
    browser_cookie = request.cookies.get(settings.invitation_handoff_cookie_name)
    try:
        return await acceptance_service.preview_invitation(
            session,
            invitation_id=parsed_id,
            session_data=auth_user.session_data,
            issuer=auth_user.issuer,
            subject=auth_user.subject,
            user_id=auth_user.user_id,
            browser_cookie=browser_cookie,
        )
    except Exception as exc:
        raise _translate_accept_error(exc) from exc


@router.post(
    "/{invitation_id}/accept",
    response_model=InvitationAcceptResponseDTO,
)
async def accept_invitation(
    invitation_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> InvitationAcceptResponseDTO:
    """Atomically accept verified workspace invitation on explicit user confirmation."""
    _apply_no_store_headers(response)
    verify_invitation_csrf_or_origin(request)
    await _enforce_empty_json_body(request)
    parsed_id = _parse_invitation_uuid(invitation_id)
    auth_user = await get_current_authenticated_user(
        request, authorization=request.headers.get("authorization")
    )
    browser_cookie = request.cookies.get(settings.invitation_handoff_cookie_name)
    request_id = request.headers.get("x-request-id") or str(uuid4())
    trace_id = request.headers.get("x-trace-id") or request_id
    try:
        return await acceptance_service.accept_invitation(
            session,
            invitation_id=parsed_id,
            session_data=auth_user.session_data,
            issuer=auth_user.issuer,
            subject=auth_user.subject,
            user_id=auth_user.user_id,
            browser_cookie=browser_cookie,
            request_id=request_id,
            trace_id=trace_id,
        )
    except Exception as exc:
        raise _translate_accept_error(exc) from exc
