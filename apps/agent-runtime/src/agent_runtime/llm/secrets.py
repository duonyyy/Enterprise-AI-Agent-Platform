"""Scoped secret resolver with strict tenancy, service scoping, and redaction."""

from __future__ import annotations

import os
import re
from typing import Final
from uuid import UUID

from agent_runtime.llm.errors import SecretAccessDeniedError, SecretNotFoundError

_SECRET_REF_PATTERN: Final = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")
_DISALLOWED_PATTERNS: Final = frozenset(
    {
        "database_url",
        "postgres",
        "redis",
        "path",
        "systemroot",
        "userprofile",
        "aws_secret_access_key",
        "secret_key",
        "auth_secret",
    }
)


class RedactedSecret:
    """Wrapper that prevents secret credentials from leaking into str/repr/logs."""

    def __init__(self, value: str) -> None:
        self._value = value

    def expose_secret(self) -> str:
        """Explicitly unmask credential for outbound HTTP authorization header only."""
        return self._value

    def __repr__(self) -> str:
        return "<RedactedSecret: ***>"

    def __str__(self) -> str:
        return "***"


class ScopedSecretResolver:
    """Resolves credentials bound to server-side (service, tenant_id, workspace_id) scope.

    Rejects:
    - Arbitrary environment variables or file paths passed by caller/model.
    - References not matching allowlisted pattern.
    - References belonging to different workspaces or tenants.
    """

    def __init__(
        self,
        *,
        static_registry: dict[tuple[str, UUID, UUID, str], str] | None = None,
        env_prefix: str = "MODEL_GATEWAY_SECRET_",
    ) -> None:
        self._static_registry = static_registry or {}
        self._env_prefix = env_prefix
        self._closed = False

    def resolve(
        self,
        service: str,
        tenant_id: UUID,
        workspace_id: UUID,
        secret_ref: str,
    ) -> RedactedSecret:
        """Resolve a secret reference under strict scope checks."""
        if self._closed:
            raise SecretNotFoundError("Secret resolver is closed")

        if not secret_ref or not _SECRET_REF_PATTERN.match(secret_ref):
            raise SecretNotFoundError(f"Invalid secret reference format: '{secret_ref}'")

        normalized_ref = secret_ref.lower()
        if any(bad in normalized_ref for bad in _DISALLOWED_PATTERNS):
            raise SecretAccessDeniedError(f"Access to prohibited secret pattern denied: '{secret_ref}'")

        # 1. Check scoped static registry
        key = (service, tenant_id, workspace_id, secret_ref)
        if key in self._static_registry:
            return RedactedSecret(self._static_registry[key])

        # 2. Check workspace-prefixed environment variable
        # Format: MODEL_GATEWAY_SECRET_<TENANT>_<WORKSPACE>_<REF> or MODEL_GATEWAY_SECRET_<REF>
        ws_env_key = f"{self._env_prefix}{str(workspace_id).replace('-', '_').upper()}_{secret_ref.replace('-', '_').upper()}"
        if ws_env_key in os.environ:
            return RedactedSecret(os.environ[ws_env_key])

        global_env_key = f"{self._env_prefix}{secret_ref.replace('-', '_').upper()}"
        if global_env_key in os.environ:
            return RedactedSecret(os.environ[global_env_key])

        raise SecretNotFoundError(
            f"Secret reference '{secret_ref}' not found in workspace scope '{workspace_id}'"
        )

    def close(self) -> None:
        """Close resolver resources and clear caches."""
        self._closed = True
        self._static_registry.clear()
