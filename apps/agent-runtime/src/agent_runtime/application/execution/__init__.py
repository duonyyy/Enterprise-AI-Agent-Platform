"""Execution application module (DEC-07 / DATA-07)."""

from agent_runtime.application.execution.grant import (
    DEFAULT_KEY_ID,
    ExecutionGrant,
    ExecutionGrantError,
    ExecutionGrantTamperError,
    ExecutionKeyUnavailableError,
    build_grant_aad,
    decrypt_execution_grant,
    encrypt_execution_grant,
    normalize_encryption_key,
)

__all__ = [
    "DEFAULT_KEY_ID",
    "ExecutionGrant",
    "ExecutionGrantError",
    "ExecutionGrantTamperError",
    "ExecutionKeyUnavailableError",
    "build_grant_aad",
    "decrypt_execution_grant",
    "encrypt_execution_grant",
    "normalize_encryption_key",
]
