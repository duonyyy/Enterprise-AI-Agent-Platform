"""FastAPI Router for global browser-bound invitation handoff, preview, and acceptance (ADR-020)."""

from __future__ import annotations

import ipaddress
import json
import time
from datetime import datetime
from typing import Annotated

from control_api.core.auth import (
    get_current_authenticated_user,
    verify_invitation_csrf_or_origin,
)
from control_api.core.cookies import set_invitation_handoff_cookie
from control_api.core.dependencies import DBSession
from control_api.core.errors import RepositoryUnavailableError
from control_api.core.settings import settings
from control_api.modules.workspaces.api._http_helpers import (
    extract_correlation_ids,
    parse_uuid_path_param,
    translate_domain_error,
)
from control_api.modules.workspaces.api.dependencies import HandoffStoreDep
from control_api.modules.workspaces.dto import (
    InvitationAcceptResponseDTO,
    InvitationHandoffRequestDTO,
    InvitationHandoffResponseDTO,
    InvitationPreviewResponseDTO,
)
from control_api.modules.workspaces.models.errors import (
    InvitationError,
    WorkspaceAccessDeniedError,
)
from control_api.modules.workspaces.repositories.invitation_repository import InvitationRepository
from control_api.modules.workspaces.services.invitation_acceptance_service import (
    InvitationAcceptanceService,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

router = APIRouter(prefix="/v1/invitations", tags=["invitations"])
acceptance_repository = InvitationRepository()


def get_handoff_service(store: HandoffStoreDep) -> InvitationAcceptanceService:
    """Resolve the DB-free invitation handoff service backed by the active Redis store."""
    return InvitationAcceptanceService(store=store)


def get_acceptance_service(
    session: DBSession,
    store: HandoffStoreDep,
) -> InvitationAcceptanceService:
    """Resolve the session-bound invitation acceptance service."""
    return InvitationAcceptanceService(
        repository=acceptance_repository.bind(session),
        store=store,
    )


HandoffServiceDep = Annotated[InvitationAcceptanceService, Depends(get_handoff_service)]
AcceptanceServiceDep = Annotated[InvitationAcceptanceService, Depends(get_acceptance_service)]


def _apply_no_store_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"


def _parse_trusted_networks(
    trusted_cidrs: tuple[str, ...] | list[str],
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    return [ipaddress.ip_network(cidr, strict=False) for cidr in trusted_cidrs]


def _extract_trusted_client_ip(request: Request) -> str:
    peer_host = request.client.host if request.client and request.client.host else "unknown"
    trusted_cidrs = settings.trusted_proxy_cidrs
    if not trusted_cidrs or peer_host == "unknown":
        return peer_host
    try:
        peer_ip = ipaddress.ip_address(peer_host)
        trusted_networks = _parse_trusted_networks(trusted_cidrs)
    except ValueError:
        return peer_host
    if not any(peer_ip in net for net in trusted_networks):
        return peer_host

    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    for candidate in reversed(parts):
        try:
            cand_ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not any(cand_ip in net for net in trusted_networks):
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_request_body",
                "message": "Request body must be empty",
            },
        ) from exc
    if parsed != {}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_request_body",
                "message": "Request body must be empty",
            },
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
    service: HandoffServiceDep,
) -> InvitationHandoffResponseDTO:
    """Persist encrypted browser-bound handoff from email link fragment before OIDC/preview."""
    _apply_no_store_headers(response)
    verify_invitation_csrf_or_origin(request)
    parsed_id = parse_uuid_path_param(
        invitation_id,
        code="invalid_invitation_id",
        message="invitation_id must be a valid UUID",
    )
    request_id, trace_id = extract_correlation_ids(request)
    existing_cookie = request.cookies.get(settings.invitation_handoff_cookie_name)
    client_ip = _extract_trusted_client_ip(request)
    try:
        dto, browser_token = await service.create_handoff_async(
            invitation_id=parsed_id,
            raw_token=payload.token,
            existing_browser_cookie=existing_cookie,
            client_ip=client_ip,
        )
    except (InvitationError, WorkspaceAccessDeniedError, RepositoryUnavailableError) as exc:
        raise translate_domain_error(
            exc, scope="accept", request_id=request_id, trace_id=trace_id
        ) from exc

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
    service: AcceptanceServiceDep,
) -> InvitationPreviewResponseDTO:
    """Preview invitation metadata after verifying application session, fresh OIDC evidence, and handoff."""
    _apply_no_store_headers(response)
    verify_invitation_csrf_or_origin(request)
    await _enforce_empty_json_body(request)
    parsed_id = parse_uuid_path_param(
        invitation_id,
        code="invalid_invitation_id",
        message="invitation_id must be a valid UUID",
    )
    request_id, trace_id = extract_correlation_ids(request)
    auth_user = await get_current_authenticated_user(
        request, authorization=request.headers.get("authorization")
    )
    browser_cookie = request.cookies.get(settings.invitation_handoff_cookie_name)
    try:
        return await service.preview_invitation(
            invitation_id=parsed_id,
            session_data=auth_user.session_data,
            issuer=auth_user.issuer,
            subject=auth_user.subject,
            user_id=auth_user.user_id,
            browser_cookie=browser_cookie,
        )
    except (InvitationError, WorkspaceAccessDeniedError, RepositoryUnavailableError) as exc:
        raise translate_domain_error(
            exc, scope="accept", request_id=request_id, trace_id=trace_id
        ) from exc


@router.post(
    "/{invitation_id}/accept",
    response_model=InvitationAcceptResponseDTO,
)
async def accept_invitation(
    invitation_id: str,
    request: Request,
    response: Response,
    service: AcceptanceServiceDep,
) -> InvitationAcceptResponseDTO:
    """Atomically accept verified workspace invitation on explicit user confirmation."""
    _apply_no_store_headers(response)
    verify_invitation_csrf_or_origin(request)
    await _enforce_empty_json_body(request)
    parsed_id = parse_uuid_path_param(
        invitation_id,
        code="invalid_invitation_id",
        message="invitation_id must be a valid UUID",
    )
    auth_user = await get_current_authenticated_user(
        request, authorization=request.headers.get("authorization")
    )
    browser_cookie = request.cookies.get(settings.invitation_handoff_cookie_name)
    request_id, trace_id = extract_correlation_ids(request)
    try:
        return await service.accept_invitation(
            invitation_id=parsed_id,
            session_data=auth_user.session_data,
            issuer=auth_user.issuer,
            subject=auth_user.subject,
            user_id=auth_user.user_id,
            browser_cookie=browser_cookie,
            request_id=request_id,
            trace_id=trace_id,
        )
    except (InvitationError, WorkspaceAccessDeniedError, RepositoryUnavailableError) as exc:
        raise translate_domain_error(
            exc, scope="accept", request_id=request_id, trace_id=trace_id
        ) from exc
