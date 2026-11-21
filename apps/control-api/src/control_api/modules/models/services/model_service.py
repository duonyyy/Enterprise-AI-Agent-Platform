"""Model configuration domain service with validation, audit trails, and tenancy checks."""

from __future__ import annotations

import logging
import re
from typing import Any, Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from control_api.core.audit import AuditEventWrite, write_audit_event
from control_api.modules.agents.repositories.agent_repository import (
    _MODEL_SETTINGS_ALLOWLIST,
    _MODEL_SETTINGS_DENYLIST,
)
from control_api.modules.models.dto.requests import (
    CreateModelConfigDTO,
    UpdateModelConfigDTO,
)
from control_api.modules.models.dto.responses import (
    ModelConfigDTO,
    ModelConfigPageDTO,
)
from control_api.modules.models.models.errors import (
    ModelConfigNotFoundError,
    ModelConfigValidationError,
)
from control_api.modules.models.models.models import ModelConfig
from control_api.modules.models.repositories.model_repository import ModelRepository

logger = logging.getLogger(__name__)

_SECRET_REF_REGEX: Final = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")
_NAME_REGEX: Final = re.compile(r"^[a-zA-Z0-9_\-\.\s]{1,128}$")


def _validate_settings_payload(settings: dict[str, Any]) -> dict[str, Any]:
    """Validate settings payload: strictly reject credentials and unapproved keys."""
    if not isinstance(settings, dict):
        raise ModelConfigValidationError("Model settings must be a JSON object", code="invalid_settings")

    sanitized: dict[str, Any] = {}
    for key, value in settings.items():
        lower_key = key.lower()
        if any(deny in lower_key for deny in _MODEL_SETTINGS_DENYLIST):
            raise ModelConfigValidationError(
                f"Model setting '{key}' contains forbidden credential pattern",
                code="credential_exposure_rejected",
            )
        if key not in _MODEL_SETTINGS_ALLOWLIST:
            raise ModelConfigValidationError(
                f"Model setting '{key}' is not in the approved settings allowlist",
                code="unapproved_model_setting",
            )
        sanitized[key] = value

    return sanitized


class ModelService:
    """Orchestrates model configuration CRUD, RBAC assertions, and immutable audit logs."""

    def __init__(self, repository: ModelRepository) -> None:
        self._repository = repository

    def _to_dto(self, entity: ModelConfig) -> ModelConfigDTO:
        return ModelConfigDTO(
            id=entity.id,
            tenantId=entity.tenant_id,
            workspaceId=entity.workspace_id,
            name=entity.name,
            provider=entity.provider,
            providerModelId=entity.provider_model_id,
            status=entity.status,
            settings=entity.settings,
            secretRef=entity.secret_ref,
            createdAt=entity.created_at,
        )

    async def create_model_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_subject: str,
        dto: CreateModelConfigDTO,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> ModelConfigDTO:
        """Create a new model configuration within workspace scope and write transactional audit."""
        clean_name = dto.name.strip()
        if not _NAME_REGEX.match(clean_name):
            raise ModelConfigValidationError(
                f"Model name '{dto.name}' contains invalid characters",
                code="invalid_name",
            )

        if dto.secret_ref:
            clean_secret_ref = dto.secret_ref.strip()
            if not _SECRET_REF_REGEX.match(clean_secret_ref):
                raise ModelConfigValidationError(
                    f"Secret reference '{dto.secret_ref}' contains invalid characters",
                    code="invalid_secret_ref",
                )
        else:
            clean_secret_ref = None

        validated_settings = _validate_settings_payload(dto.settings)

        config = await self._repository.create_model_config(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=clean_name,
            provider=dto.provider.strip(),
            provider_model_id=dto.provider_model_id.strip(),
            settings=validated_settings,
            secret_ref=clean_secret_ref,
        )

        # Audit emission within same transaction
        audit_event = AuditEventWrite(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_subject=actor_subject,
            action="model:create",
            resource_type="model_config",
            resource_id=str(config.id),
            outcome="succeeded",
            request_id=request_id,
            trace_id=trace_id,
            details_redacted={
                "name": config.name,
                "provider": config.provider,
                "provider_model_id": config.provider_model_id,
                "status": config.status,
                "has_secret_ref": config.secret_ref is not None,
            },
        )
        await write_audit_event(session, audit_event)

        return self._to_dto(config)

    async def get_model_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        model_id: UUID,
    ) -> ModelConfigDTO:
        """Fetch a single model configuration by ID."""
        config = await self._repository.get_model_config_by_id(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            model_id=model_id,
        )
        if config is None:
            raise ModelConfigNotFoundError(f"Model configuration '{model_id}' not found")
        return self._to_dto(config)

    async def list_model_configs(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        limit: int = 25,
        cursor: str | None = None,
        status_filter: str | None = None,
    ) -> ModelConfigPageDTO:
        """List model configurations with pagination under RLS."""
        configs, next_cursor, total = await self._repository.list_model_configs(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            limit=limit,
            cursor=cursor,
            status_filter=status_filter,
        )
        return ModelConfigPageDTO(
            items=[self._to_dto(c) for c in configs],
            nextCursor=next_cursor,
            totalCount=total,
        )

    async def update_model_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        model_id: UUID,
        actor_subject: str,
        dto: UpdateModelConfigDTO,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> ModelConfigDTO:
        """Update model configuration settings or name within workspace scope."""
        name: str | None = None
        if dto.name is not None:
            name = dto.name.strip()
            if not _NAME_REGEX.match(name):
                raise ModelConfigValidationError(
                    f"Model name '{dto.name}' contains invalid characters",
                    code="invalid_name",
                )

        settings: dict[str, Any] | None = None
        if dto.settings is not None:
            settings = _validate_settings_payload(dto.settings)

        secret_ref: str | None = None
        if dto.secret_ref is not None:
            secret_ref = dto.secret_ref.strip()
            if not _SECRET_REF_REGEX.match(secret_ref):
                raise ModelConfigValidationError(
                    f"Secret reference '{dto.secret_ref}' contains invalid characters",
                    code="invalid_secret_ref",
                )

        config = await self._repository.update_model_config(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            model_id=model_id,
            name=name,
            settings=settings,
            secret_ref=secret_ref,
        )

        audit_event = AuditEventWrite(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_subject=actor_subject,
            action="model:update",
            resource_type="model_config",
            resource_id=str(config.id),
            outcome="succeeded",
            request_id=request_id,
            trace_id=trace_id,
            details_redacted={
                "name": config.name,
                "provider": config.provider,
                "provider_model_id": config.provider_model_id,
                "status": config.status,
                "has_secret_ref": config.secret_ref is not None,
            },
        )
        await write_audit_event(session, audit_event)

        return self._to_dto(config)

    async def disable_model_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        model_id: UUID,
        actor_subject: str,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> ModelConfigDTO:
        """Disable a model configuration to prevent new executions."""
        config = await self._repository.disable_model_config(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            model_id=model_id,
        )

        audit_event = AuditEventWrite(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_subject=actor_subject,
            action="model:disable",
            resource_type="model_config",
            resource_id=str(config.id),
            outcome="succeeded",
            request_id=request_id,
            trace_id=trace_id,
            details_redacted={
                "name": config.name,
                "provider": config.provider,
                "provider_model_id": config.provider_model_id,
                "status": "disabled",
                "has_secret_ref": config.secret_ref is not None,
            },
        )
        await write_audit_event(session, audit_event)

        return self._to_dto(config)
