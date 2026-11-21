"""Integration & API tests for Model Registry REST endpoints under RBAC and RLS (G5-03, G5-18)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from platform_common.principal import AuthenticatedPrincipal

from control_api.core.dependencies import get_current_principal, get_db_session
from control_api.modules.models.api.dependencies import get_model_service
from control_api.modules.models.api.router import router as models_router
from control_api.modules.models.models.errors import (
    ModelConfigConflictError,
    ModelConfigNotFoundError,
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
def test_app(mock_repository: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(models_router)

    service = ModelService(mock_repository)
    app.dependency_overrides[get_model_service] = lambda: service

    dummy_session = AsyncMock()
    dummy_audit_res = MagicMock()
    dummy_audit_res.mappings.return_value.one.return_value = {"id": uuid4()}
    dummy_session.execute.return_value = dummy_audit_res
    app.dependency_overrides[get_db_session] = lambda: dummy_session

    return app


class TestModelRegistryAPI:
    def test_owner_can_create_model(self, test_app: FastAPI, mock_repository: MagicMock) -> None:
        tenant_id = uuid4()
        workspace_id = uuid4()
        model_id = uuid4()
        principal = AuthenticatedPrincipal(
            user_id=str(uuid4()),
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            roles=["owner"],
            permissions=["workspace:*", "model:*"],
        )
        test_app.dependency_overrides[get_current_principal] = lambda: principal

        mock_repository.create_model_config.return_value = ModelConfig(
            id=model_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name="Claude 3.5",
            provider="anthropic",
            provider_model_id="claude-3-5-sonnet",
            secret_ref="anthropic_key",
            settings={"temperature": 0.5},
            status="active",
            created_at=datetime.now(timezone.utc),
        )

        client = TestClient(test_app)
        res = client.post(
            f"/v1/workspaces/{workspace_id}/models",
            json={
                "name": "Claude 3.5",
                "provider": "anthropic",
                "providerModelId": "claude-3-5-sonnet",
                "secretRef": "anthropic_key",
                "settings": {"temperature": 0.5},
            },
        )
        assert res.status_code == status.HTTP_201_CREATED
        data = res.json()
        assert data["id"] == str(model_id)
        assert data["name"] == "Claude 3.5"
        assert data["status"] == "active"

    def test_developer_cannot_create_model_returns_403(
        self, test_app: FastAPI, mock_repository: MagicMock
    ) -> None:
        tenant_id = uuid4()
        workspace_id = uuid4()
        principal = AuthenticatedPrincipal(
            user_id=str(uuid4()),
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            roles=["developer"],
            permissions=["workspace:read", "agent:read", "model:read"],
        )
        test_app.dependency_overrides[get_current_principal] = lambda: principal

        client = TestClient(test_app)
        res = client.post(
            f"/v1/workspaces/{workspace_id}/models",
            json={
                "name": "Disallowed Model",
                "provider": "openai",
                "providerModelId": "gpt-4o",
            },
        )
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_developer_can_list_and_read_models(
        self, test_app: FastAPI, mock_repository: MagicMock
    ) -> None:
        tenant_id = uuid4()
        workspace_id = uuid4()
        model_id = uuid4()
        principal = AuthenticatedPrincipal(
            user_id=str(uuid4()),
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            roles=["developer"],
            permissions=["workspace:read", "agent:read", "model:read"],
        )
        test_app.dependency_overrides[get_current_principal] = lambda: principal

        mock_repository.list_model_configs.return_value = (
            [
                ModelConfig(
                    id=model_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    name="Claude 3.5",
                    provider="anthropic",
                    provider_model_id="claude-3-5-sonnet",
                    secret_ref="anthropic_key",
                    settings={},
                    status="active",
                    created_at=datetime.now(timezone.utc),
                )
            ],
            None,
            1,
        )

        client = TestClient(test_app)
        res = client.get(f"/v1/workspaces/{workspace_id}/models")
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["totalCount"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == str(model_id)

    def test_duplicate_name_returns_409_conflict(
        self, test_app: FastAPI, mock_repository: MagicMock
    ) -> None:
        tenant_id = uuid4()
        workspace_id = uuid4()
        principal = AuthenticatedPrincipal(
            user_id=str(uuid4()),
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            roles=["admin"],
            permissions=["workspace:*", "model:*"],
        )
        test_app.dependency_overrides[get_current_principal] = lambda: principal

        mock_repository.create_model_config.side_effect = ModelConfigConflictError(
            "Model configuration with name 'Existing Model' already exists"
        )

        client = TestClient(test_app)
        res = client.post(
            f"/v1/workspaces/{workspace_id}/models",
            json={
                "name": "Existing Model",
                "provider": "openai",
                "providerModelId": "gpt-4o",
            },
        )
        assert res.status_code == status.HTTP_409_CONFLICT
        assert res.json()["detail"]["code"] == "model_conflict"

    def test_invalid_settings_returns_422(
        self, test_app: FastAPI, mock_repository: MagicMock
    ) -> None:
        tenant_id = uuid4()
        workspace_id = uuid4()
        principal = AuthenticatedPrincipal(
            user_id=str(uuid4()),
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            roles=["admin"],
            permissions=["workspace:*", "model:*"],
        )
        test_app.dependency_overrides[get_current_principal] = lambda: principal

        client = TestClient(test_app)
        res = client.post(
            f"/v1/workspaces/{workspace_id}/models",
            json={
                "name": "Leaked Key Model",
                "provider": "openai",
                "providerModelId": "gpt-4o",
                "settings": {"api_key": "sk-raw-secret-key"},
            },
        )
        assert res.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert res.json()["detail"]["code"] == "credential_exposure_rejected"

    def test_get_missing_model_returns_404(
        self, test_app: FastAPI, mock_repository: MagicMock
    ) -> None:
        tenant_id = uuid4()
        workspace_id = uuid4()
        model_id = uuid4()
        principal = AuthenticatedPrincipal(
            user_id=str(uuid4()),
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            roles=["viewer"],
            permissions=["model:read"],
        )
        test_app.dependency_overrides[get_current_principal] = lambda: principal
        mock_repository.get_model_config_by_id.return_value = None

        client = TestClient(test_app)
        res = client.get(f"/v1/workspaces/{workspace_id}/models/{model_id}")
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"]["code"] == "model_not_found"

    def test_disable_model_by_admin_succeeds(
        self, test_app: FastAPI, mock_repository: MagicMock
    ) -> None:
        tenant_id = uuid4()
        workspace_id = uuid4()
        model_id = uuid4()
        principal = AuthenticatedPrincipal(
            user_id=str(uuid4()),
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            roles=["admin"],
            permissions=["model:*"],
        )
        test_app.dependency_overrides[get_current_principal] = lambda: principal

        mock_repository.disable_model_config.return_value = ModelConfig(
            id=model_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name="Decommissioned Model",
            provider="openai",
            provider_model_id="gpt-3.5",
            secret_ref=None,
            settings={},
            status="disabled",
            created_at=datetime.now(timezone.utc),
        )

        client = TestClient(test_app)
        res = client.post(f"/v1/workspaces/{workspace_id}/models/{model_id}/disable")
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["status"] == "disabled"
