"""Repositories and external cache adapters for the Workspaces module (SPEC-WORKSPACE-001)."""

from control_api.modules.workspaces.repositories.handoff_store import (
    BaseInvitationHandoffStore,
    InMemoryInvitationHandoffStore,
    RedisInvitationHandoffStore,
    close_invitation_handoff_store,
    create_invitation_handoff_store,
    get_invitation_handoff_store,
)
from control_api.modules.workspaces.repositories.invitation_repository import (
    InvitationRepository,
    SessionBoundInvitationRepository,
)
from control_api.modules.workspaces.repositories.membership_repository import (
    MembershipRepository,
    SessionBoundMembershipRepository,
)
from control_api.modules.workspaces.repositories.workspace_repository import (
    SessionBoundWorkspaceRepository,
    WorkspaceRepository,
    workspace_repository,
)

__all__ = [
    "BaseInvitationHandoffStore",
    "InMemoryInvitationHandoffStore",
    "InvitationRepository",
    "MembershipRepository",
    "RedisInvitationHandoffStore",
    "SessionBoundInvitationRepository",
    "SessionBoundMembershipRepository",
    "SessionBoundWorkspaceRepository",
    "WorkspaceRepository",
    "close_invitation_handoff_store",
    "create_invitation_handoff_store",
    "get_invitation_handoff_store",
    "workspace_repository",
]
