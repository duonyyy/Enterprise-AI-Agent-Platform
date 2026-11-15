"""Canonical JSON Schema validation for AgentSpec.

Authority:
- ADR-004: Schema-first contracts at trust boundaries.
- SPEC-AGENT-001: agent-spec.v1.schema.json canonical specification.
- TP-STEP04 (D04-01): Validate raw JSON input against canonical schema before
  Pydantic coercion, defaults, or serialization.
"""

from __future__ import annotations

import importlib.resources
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class AgentSpecValidationError(ValueError):
    """Raised when raw JSON fails canonical agent-spec.v1 schema validation."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors: list[str] = errors or [message]


def _resolve_schema_path() -> Path:
    """Resolve the canonical schema location across dev, container, and wheel installs."""
    # 1. Explicit override via environment variable
    env_override = os.environ.get("AGENT_SPEC_SCHEMA_PATH")
    if env_override:
        candidate = Path(env_override)
        if candidate.is_file():
            return candidate

    # 2. Packaged resource inside installed wheel / container
    try:
        packaged = importlib.resources.files("agent_contracts").joinpath(
            "schemas/agent-spec.v1.schema.json"
        )
        if packaged.is_file():
            return Path(str(packaged))
    except (TypeError, ModuleNotFoundError, AttributeError):
        pass

    # 3. Source repository monorepo checkout (relative to this file)
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        probe = parent / "contracts" / "jsonschema" / "agent-spec.v1.schema.json"
        if probe.is_file():
            return probe

    raise FileNotFoundError(
        "Canonical schema 'agent-spec.v1.schema.json' could not be located. "
        "Ensure the package is installed with packaged schemas or AGENT_SPEC_SCHEMA_PATH is set."
    )


@lru_cache(maxsize=1)
def load_canonical_agent_spec_schema() -> dict[str, Any]:
    """Load and cache the canonical agent-spec.v1 JSON Schema object."""
    schema_path = _resolve_schema_path()
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=1)
def get_agent_spec_validator() -> Draft202012Validator:
    """Return a cached Draft202012Validator instance for agent-spec.v1."""
    schema = load_canonical_agent_spec_schema()
    return Draft202012Validator(schema)


def validate_agent_spec_raw(raw: Any) -> list[str]:
    """Validate a raw, untrusted JSON object directly against the canonical schema.

    Must be called on raw parsed JSON BEFORE Pydantic models apply defaults,
    type coercions, or strip invalid nulls/extra fields.

    Returns:
        An empty list if valid.

    Raises:
        AgentSpecValidationError: If raw data is invalid according to the canonical schema.
    """
    if not isinstance(raw, dict):
        error_msg = f"AgentSpec must be a JSON object, got {type(raw).__name__}"
        raise AgentSpecValidationError(error_msg, [error_msg])

    validator = get_agent_spec_validator()
    raw_errors = sorted(validator.iter_errors(raw), key=lambda e: (list(e.path), e.message))

    if not raw_errors:
        return []

    error_messages: list[str] = []
    for err in raw_errors:
        path = ".".join(str(p) for p in err.path) if err.path else "root"
        error_messages.append(f"[{path}] {err.message}")

    summary = (
        f"AgentSpec raw validation failed with {len(error_messages)} error(s): "
        f"{'; '.join(error_messages)}"
    )
    raise AgentSpecValidationError(summary, error_messages)


__all__ = [
    "AgentSpecValidationError",
    "load_canonical_agent_spec_schema",
    "validate_agent_spec_raw",
]
