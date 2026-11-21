"""PostgreSQL repository for catalog.model_configs under RLS."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from control_api.modules.models.models.errors import (
    ModelConfigConflictError,
    ModelConfigDatabaseUnavailableError,
    ModelConfigNotFoundError,
)
from control_api.modules.models.models.models import ModelConfig

logger = logging.getLogger(__name__)


class ModelRepository(Protocol):
    async def create_model_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        name: str,
        provider: str,
        provider_model_id: str,
        settings: dict[str, Any],
        secret_ref: str | None,
    ) -> ModelConfig:
        ...

    async def get_model_config_by_id(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        model_id: UUID,
        for_share: bool = False,
    ) -> ModelConfig | None:
        ...

    async def get_model_config_by_name(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        name: str,
    ) -> ModelConfig | None:
        ...

    async def list_model_configs(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        limit: int = 25,
        cursor: str | None = None,
        status_filter: str | None = None,
    ) -> tuple[Sequence[ModelConfig], str | None, int]:
        ...

    async def update_model_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        model_id: UUID,
        name: str | None = None,
        settings: dict[str, Any] | None = None,
        secret_ref: str | None = None,
    ) -> ModelConfig:
        ...

    async def disable_model_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        model_id: UUID,
    ) -> ModelConfig:
        ...


class PostgresModelRepository(ModelRepository):
    """PostgreSQL adapter querying catalog.model_configs."""

    async def create_model_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        name: str,
        provider: str,
        provider_model_id: str,
        settings: dict[str, Any],
        secret_ref: str | None,
    ) -> ModelConfig:
        stmt = text(
            "INSERT INTO catalog.model_configs ("
            "    tenant_id, workspace_id, name, provider, provider_model_id, settings, secret_ref, status"
            ") VALUES ("
            "    :tenant_id, :workspace_id, :name, :provider, :provider_model_id, CAST(:settings AS jsonb), :secret_ref, 'active'"
            ") RETURNING id, tenant_id, workspace_id, name, provider, provider_model_id, settings, secret_ref, status, created_at"
        )
        try:
            result = await session.execute(
                stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "name": name,
                    "provider": provider,
                    "provider_model_id": provider_model_id,
                    "settings": json.dumps(settings),
                    "secret_ref": secret_ref,
                },
            )
            row = result.mappings().one()
            return ModelConfig(
                id=row["id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                name=row["name"],
                provider=row["provider"],
                provider_model_id=row["provider_model_id"],
                secret_ref=row["secret_ref"],
                settings=dict(row["settings"]) if row["settings"] else {},
                status=str(row["status"]),
                created_at=row["created_at"],
            )
        except IntegrityError as exc:
            logger.warning("Model config insert integrity conflict: %s", type(exc).__name__)
            raise ModelConfigConflictError(
                f"Model configuration with name '{name}' already exists in workspace"
            ) from exc
        except SQLAlchemyError as exc:
            logger.warning("Model config database error: %s", type(exc).__name__)
            raise ModelConfigDatabaseUnavailableError() from exc

    async def get_model_config_by_id(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        model_id: UUID,
        for_share: bool = False,
    ) -> ModelConfig | None:
        lock_clause = " FOR SHARE" if for_share else ""
        stmt = text(
            "SELECT id, tenant_id, workspace_id, name, provider, provider_model_id, settings, secret_ref, status, created_at "
            "FROM catalog.model_configs "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :model_id"
            f"{lock_clause}"
        )
        try:
            result = await session.execute(
                stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "model_id": model_id},
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            return ModelConfig(
                id=row["id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                name=row["name"],
                provider=row["provider"],
                provider_model_id=row["provider_model_id"],
                secret_ref=row["secret_ref"],
                settings=dict(row["settings"]) if row["settings"] else {},
                status=str(row["status"]),
                created_at=row["created_at"],
            )
        except SQLAlchemyError as exc:
            logger.warning("get_model_config_by_id database error: %s", type(exc).__name__)
            raise ModelConfigDatabaseUnavailableError() from exc

    async def get_model_config_by_name(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        name: str,
    ) -> ModelConfig | None:
        stmt = text(
            "SELECT id, tenant_id, workspace_id, name, provider, provider_model_id, settings, secret_ref, status, created_at "
            "FROM catalog.model_configs "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND name = :name"
        )
        try:
            result = await session.execute(
                stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "name": name},
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            return ModelConfig(
                id=row["id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                name=row["name"],
                provider=row["provider"],
                provider_model_id=row["provider_model_id"],
                secret_ref=row["secret_ref"],
                settings=dict(row["settings"]) if row["settings"] else {},
                status=str(row["status"]),
                created_at=row["created_at"],
            )
        except SQLAlchemyError as exc:
            logger.warning("get_model_config_by_name database error: %s", type(exc).__name__)
            raise ModelConfigDatabaseUnavailableError() from exc

    async def list_model_configs(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        limit: int = 25,
        cursor: str | None = None,
        status_filter: str | None = None,
    ) -> tuple[Sequence[ModelConfig], str | None, int]:
        count_stmt = text(
            "SELECT COUNT(*) AS total FROM catalog.model_configs "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id"
            + (" AND status = :status" if status_filter else "")
        )
        base_query = (
            "SELECT id, tenant_id, workspace_id, name, provider, provider_model_id, settings, secret_ref, status, created_at "
            "FROM catalog.model_configs "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id"
        )
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "limit": limit + 1,
        }
        if status_filter:
            base_query += " AND status = :status"
            params["status"] = status_filter

        if cursor:
            base_query += " AND (created_at, id) < (SELECT created_at, id FROM catalog.model_configs WHERE id = :cursor_id)"
            try:
                params["cursor_id"] = UUID(cursor)
            except ValueError:
                pass

        base_query += " ORDER BY created_at DESC, id DESC LIMIT :limit"

        try:
            count_res = await session.execute(
                count_stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id, **({"status": status_filter} if status_filter else {})},
            )
            total = int(count_res.scalar_one() or 0)

            result = await session.execute(text(base_query), params)
            rows = result.mappings().all()

            has_more = len(rows) > limit
            items_to_return = rows[:limit]
            next_cursor = str(items_to_return[-1]["id"]) if has_more and items_to_return else None

            configs = [
                ModelConfig(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    workspace_id=row["workspace_id"],
                    name=row["name"],
                    provider=row["provider"],
                    provider_model_id=row["provider_model_id"],
                    secret_ref=row["secret_ref"],
                    settings=dict(row["settings"]) if row["settings"] else {},
                    status=str(row["status"]),
                    created_at=row["created_at"],
                )
                for row in items_to_return
            ]
            return configs, next_cursor, total
        except SQLAlchemyError as exc:
            logger.warning("list_model_configs database error: %s", type(exc).__name__)
            raise ModelConfigDatabaseUnavailableError() from exc

    async def update_model_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        model_id: UUID,
        name: str | None = None,
        settings: dict[str, Any] | None = None,
        secret_ref: str | None = None,
    ) -> ModelConfig:
        clauses = []
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "model_id": model_id,
        }
        if name is not None:
            clauses.append("name = :name")
            params["name"] = name
        if settings is not None:
            clauses.append("settings = CAST(:settings AS jsonb)")
            params["settings"] = json.dumps(settings)
        if secret_ref is not None:
            clauses.append("secret_ref = :secret_ref")
            params["secret_ref"] = secret_ref

        if not clauses:
            existing = await self.get_model_config_by_id(
                session, tenant_id=tenant_id, workspace_id=workspace_id, model_id=model_id
            )
            if existing is None:
                raise ModelConfigNotFoundError(f"Model config '{model_id}' not found")
            return existing

        update_stmt = text(
            f"UPDATE catalog.model_configs SET {', '.join(clauses)} "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :model_id "
            "RETURNING id, tenant_id, workspace_id, name, provider, provider_model_id, settings, secret_ref, status, created_at"
        )
        try:
            result = await session.execute(update_stmt, params)
            row = result.mappings().one_or_none()
            if row is None:
                raise ModelConfigNotFoundError(f"Model config '{model_id}' not found")
            return ModelConfig(
                id=row["id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                name=row["name"],
                provider=row["provider"],
                provider_model_id=row["provider_model_id"],
                secret_ref=row["secret_ref"],
                settings=dict(row["settings"]) if row["settings"] else {},
                status=str(row["status"]),
                created_at=row["created_at"],
            )
        except IntegrityError as exc:
            logger.warning("update_model_config conflict: %s", type(exc).__name__)
            raise ModelConfigConflictError(f"Model name conflict during update: {exc}") from exc
        except SQLAlchemyError as exc:
            logger.warning("update_model_config database error: %s", type(exc).__name__)
            raise ModelConfigDatabaseUnavailableError() from exc

    async def disable_model_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        model_id: UUID,
    ) -> ModelConfig:
        stmt = text(
            "UPDATE catalog.model_configs SET status = 'disabled' "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :model_id "
            "RETURNING id, tenant_id, workspace_id, name, provider, provider_model_id, settings, secret_ref, status, created_at"
        )
        try:
            result = await session.execute(
                stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "model_id": model_id},
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise ModelConfigNotFoundError(f"Model config '{model_id}' not found")
            return ModelConfig(
                id=row["id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                name=row["name"],
                provider=row["provider"],
                provider_model_id=row["provider_model_id"],
                secret_ref=row["secret_ref"],
                settings=dict(row["settings"]) if row["settings"] else {},
                status=str(row["status"]),
                created_at=row["created_at"],
            )
        except SQLAlchemyError as exc:
            logger.warning("disable_model_config database error: %s", type(exc).__name__)
            raise ModelConfigDatabaseUnavailableError() from exc


model_repository = PostgresModelRepository()
