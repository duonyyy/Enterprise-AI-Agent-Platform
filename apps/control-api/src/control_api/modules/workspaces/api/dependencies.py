"""FastAPI dependency providers for the Workspaces module."""

from __future__ import annotations

from typing import Annotated

from control_api.modules.workspaces.repositories.handoff_store import (
    BaseInvitationHandoffStore,
    get_invitation_handoff_store,
)
from fastapi import Depends


def get_handoff_store_dep() -> BaseInvitationHandoffStore:
    """Resolve the active invitation handoff store through FastAPI dependency injection."""
    return get_invitation_handoff_store()


HandoffStoreDep = Annotated[BaseInvitationHandoffStore, Depends(get_handoff_store_dep)]

__all__ = ["HandoffStoreDep", "get_handoff_store_dep"]
