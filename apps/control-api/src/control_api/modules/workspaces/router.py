"""Backward-compatible router re-export for Workspaces module (SPEC-WORKSPACE-001 / Step 03-A)."""

from control_api.modules.workspaces.api.router import router

__all__ = ["router"]
