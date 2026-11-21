"""Tests for ScopedSecretResolver with strict tenancy and redaction (G5-04)."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from agent_runtime.llm.errors import SecretAccessDeniedError, SecretNotFoundError
from agent_runtime.llm.secrets import RedactedSecret, ScopedSecretResolver


class TestScopedSecretResolver:
    def test_static_registry_resolves_exact_scope(self) -> None:
        tenant_a = uuid4()
        workspace_a = uuid4()
        resolver = ScopedSecretResolver(
            static_registry={
                ("control-api", tenant_a, workspace_a, "openai_api_key"): "sk-valid-key-123",
            }
        )
        resolved = resolver.resolve("control-api", tenant_a, workspace_a, "openai_api_key")
        assert isinstance(resolved, RedactedSecret)
        assert resolved.expose_secret() == "sk-valid-key-123"
        # str and repr are masked!
        assert str(resolved) == "***"
        assert repr(resolved) == "<RedactedSecret: ***>"

    def test_cross_workspace_lookup_fails_closed(self) -> None:
        tenant_a = uuid4()
        workspace_a = uuid4()
        workspace_b = uuid4()
        resolver = ScopedSecretResolver(
            static_registry={
                ("control-api", tenant_a, workspace_a, "openai_api_key"): "sk-valid-key-123",
            }
        )
        with pytest.raises(SecretNotFoundError) as exc_info:
            resolver.resolve("control-api", tenant_a, workspace_b, "openai_api_key")
        assert "not found in workspace scope" in str(exc_info.value)

    def test_prohibited_patterns_deny_access(self) -> None:
        tenant = uuid4()
        workspace = uuid4()
        resolver = ScopedSecretResolver()
        with pytest.raises(SecretAccessDeniedError) as exc_info:
            resolver.resolve("control-api", tenant, workspace, "database_url")
        assert "prohibited secret pattern denied" in str(exc_info.value)

    def test_invalid_secret_ref_format_rejected(self) -> None:
        tenant = uuid4()
        workspace = uuid4()
        resolver = ScopedSecretResolver()
        with pytest.raises(SecretNotFoundError) as exc_info:
            resolver.resolve("control-api", tenant, workspace, "bad ref with spaces!")
        assert "Invalid secret reference format" in str(exc_info.value)

    def test_workspace_prefixed_env_variable_resolution(self) -> None:
        tenant = uuid4()
        workspace = uuid4()
        ws_str = str(workspace).replace("-", "_").upper()
        env_key = f"MODEL_GATEWAY_SECRET_{ws_str}_MY_SECRET"
        os.environ[env_key] = "secret-from-env"
        try:
            resolver = ScopedSecretResolver()
            res = resolver.resolve("agent-runtime", tenant, workspace, "my-secret")
            assert res.expose_secret() == "secret-from-env"
        finally:
            del os.environ[env_key]

    def test_closed_resolver_fails_closed(self) -> None:
        resolver = ScopedSecretResolver()
        resolver.close()
        with pytest.raises(SecretNotFoundError) as exc_info:
            resolver.resolve("control-api", uuid4(), uuid4(), "some-key")
        assert "closed" in str(exc_info.value)
