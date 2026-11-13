"""Clean Architecture boundary tests for the Workspaces module (AR-001, AR-007, CT-025)."""

from __future__ import annotations

import ast
from enum import StrEnum
from pathlib import Path

from control_api.modules.workspaces.models import enums as ws_enums

WORKSPACES_ROOT = (
    Path(__file__).resolve().parents[1] / "src" / "control_api" / "modules" / "workspaces"
)


def _iter_imported_modules(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


def test_workspaces_module_has_zero_identity_cross_module_imports() -> None:
    """Enforce AR-001: workspaces module must never import control_api.modules.identity."""
    for py_file in WORKSPACES_ROOT.rglob("*.py"):
        for mod in _iter_imported_modules(py_file):
            assert not mod.startswith("control_api.modules.identity"), (
                f"Forbidden cross-module identity import '{mod}' in {py_file}"
            )


def test_workspaces_pure_layers_have_no_framework_or_persistence_imports() -> None:
    """Enforce AR-007: models/, dto/, and services/ stay decoupled from HTTP, ORM, and caches."""
    forbidden_prefixes_by_layer = {
        "models": (
            "fastapi",
            "starlette",
            "sqlalchemy",
            "redis",
            "pydantic",
            "control_api.modules.workspaces.api",
            "control_api.modules.workspaces.dto",
            "control_api.modules.workspaces.repositories",
            "control_api.modules.workspaces.services",
        ),
        "dto": (
            "fastapi",
            "starlette",
            "sqlalchemy",
            "redis",
            "control_api.modules.workspaces.api",
            "control_api.modules.workspaces.repositories",
            "control_api.modules.workspaces.services",
        ),
        "services": (
            "fastapi",
            "starlette",
            "sqlalchemy",
            "redis",
            "control_api.modules.workspaces.api",
            "control_api.modules.workspaces.repositories",
        ),
        "repositories": (
            "fastapi",
            "starlette",
            "control_api.modules.workspaces.api",
            "control_api.modules.workspaces.services",
        ),
    }
    for layer, prefixes in forbidden_prefixes_by_layer.items():
        for py_file in (WORKSPACES_ROOT / layer).glob("*.py"):
            for mod in _iter_imported_modules(py_file):
                assert not any(
                    mod == prefix or mod.startswith(f"{prefix}.") for prefix in prefixes
                ), f"Forbidden import '{mod}' in layer '{layer}' ({py_file})"


def test_workspaces_enums_inherit_from_strenum() -> None:
    """Enforce CT-025: finite state enumerations use enum.StrEnum."""
    enum_classes = [
        value
        for name, value in vars(ws_enums).items()
        if isinstance(value, type) and issubclass(value, StrEnum) and value is not StrEnum
    ]
    assert len(enum_classes) >= 10


def test_invitation_handoff_protector_does_not_retain_raw_key() -> None:
    """Enforce AR-012 / SEC-003: protector must not retain plaintext Fernet key attribute."""
    from control_api.modules.workspaces.models.handoff import InvitationHandoffProtector
    from cryptography.fernet import Fernet

    protector = InvitationHandoffProtector(fernet_key=Fernet.generate_key().decode("ascii"))
    assert not hasattr(protector, "_raw_key")
