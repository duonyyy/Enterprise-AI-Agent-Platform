"""Unit tests for ModelService and model configuration validation (G5-03, G5-05)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from control_api.modules.models.dto.requests import (
    CreateModelConfigDTO,
    UpdateModelConfigDTO,
)
from control_api.modules.models.models.errors import (
    ModelConfigConflictError,
    ModelConfigNotFoundError,
    ModelConfigValidationError,
)
from control_api.modules.models.models.models import ModelConfig
from control_api.modules.models.services.model_service import ModelService


@pytest.fixture
def mock_repository() -> MagicMock:
    repo = MagicMock()
    repo.create_model_config = AsyncMock()
    repo.get_model_config_by_id = AsyncMock()
    repo.get_model_config_by_name = AsyncMock()
    repo.list_model_configs = AsyncMock()
    repo.update_model_config = AsyncMock()
    repo.disable_model_config = AsyncMock()
    return repo


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock()
    return session


class TestModelServiceValidation:
    def test_create_rejects_forbidden_credential_keys_in_settings(
        self, mock_repository: MagicMock, mock_session: AsyncMock
    ) -> None:
        service = ModelService(mock_repository)
        dto = CreateModelConfigDTO(
            name="Test Model",
            provider="openai",
            provider_model_id="gpt-4o",
            settings={"api_key": "sk-12345", "temperature": 0.7},
        )
        with pytest.raises(ModelConfigValidationError) as exc_info:
            asyncio.run(
                service.create_model_config(
                    mock_session,
                    tenant_id=uuid4(),
                    workspace_id=uuid4(),
                    actor_subject="user-1",
                    dto=dto,
                )
            )
        assert "forbidden credential pattern" in str(exc_info.value)
        assert not mock_repository.create_model_config.called

    def test_create_rejects_unapproved_setting_keys(
        self, mock_repository: MagicMock, mock_session: AsyncMock
    ) -> None:
        service = ModelService(mock_repository)
        dto = CreateModelConfigDTO(
            name="Test Model",
            provider="openai",
            provider_model_id="gpt-4o",
            settings={"unapproved_custom_field": "val"},
        )
        with pytest.raises(ModelConfigValidationError) as exc_info:
            asyncio.run(
                service.create_model_config(
                    mock_session,
                    tenant_id=uuid4(),
                    workspace_id=uuid4(),
                    actor_subject="user-1",
                    dto=dto,
                )
            )
        assert "not in the approved settings allowlist" in str(exc_info.value)
        assert not mock_repository.create_model_config.called

    def test_create_rejects_invalid_secret_ref(
        self, mock_repository: MagicMock, mock_session: AsyncMock
    ) -> None:
        service = ModelService(mock_repository)
        dto = CreateModelConfigDTO(
            name="Test Model",
            provider="openai",
            provider_model_id="gpt-4o",
            secret_ref="bad ref with spaces & symbols!",
        )
        with pytest.raises(ModelConfigValidationError) as exc_info:
            asyncio.run(
                service.create_model_config(
                    mock_session,
                    tenant_id=uuid4(),
                    workspace_id=uuid4(),
                    actor_subject="user-1",
                    dto=dto,
                )
            )
        assert "Secret reference" in str(exc_info.value)
        assert not mock_repository.create_model_config.called

    def test_create_succeeds_and_writes_transactional_audit(
        self, mock_repository: MagicMock, mock_session: AsyncMock
    ) -> None:
        tenant_id = uuid4()
        workspace_id = uuid4()
        model_id = uuid4()
        now = datetime.now(timezone.utc)

        mock_repository.create_model_config.return_value = ModelConfig(
            id=model_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name="Production Claude",
            provider="anthropic",
            provider_model_id="claude-3-5-sonnet",
            secret_ref="anthropic_api_key",
            settings={"temperature": 0.5, "top_p": 0.9},
            status="active",
            created_at=now,
        )

        mock_audit_res = MagicMock()
        mock_audit_res.mappings.return_value.one.return_value = {"id": uuid4()}
        mock_session.execute.return_value = mock_audit_res

        service = ModelService(mock_repository)
        dto = CreateModelConfigDTO(
            name="Production Claude",
            provider="anthropic",
            provider_model_id="claude-3-5-sonnet",
            secret_ref="anthropic_api_key",
            settings={"temperature": 0.5, "top_p": 0.9},
        )
        result = asyncio.run(
            service.create_model_config(
                mock_session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_subject="user-1",
                dto=dto,
                request_id="req-123",
            )
        )

        assert result.id == model_id
        assert result.name == "Production Claude"
        assert result.status == "active"
        assert mock_session.execute.called

    def test_disable_model_config_writes_audit(
        self, mock_repository: MagicMock, mock_session: AsyncMock
    ) -> None:
        tenant_id = uuid4()
        workspace_id = uuid4()
        model_id = uuid4()
        now = datetime.now(timezone.utc)

        mock_repository.disable_model_config.return_value = ModelConfig(
            id=model_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name="Old Model",
            provider="openai",
            provider_model_id="gpt-3.5",
            secret_ref=None,
            settings={},
            status="disabled",
            created_at=now,
        )
        mock_audit_res = MagicMock()
        mock_audit_res.mappings.return_value.one.return_value = {"id": uuid4()}
        mock_session.execute.return_value = mock_audit_res

        service = ModelService(mock_repository)
        result = asyncio.run(
            service.disable_model_config(
                mock_session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                model_id=model_id,
                actor_subject="admin-1",
            )
        )

        assert result.status == "disabled"
        assert mock_repository.disable_model_config.called
        assert mock_session.execute.called
