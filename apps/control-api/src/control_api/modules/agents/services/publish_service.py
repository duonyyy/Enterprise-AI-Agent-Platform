"""Agent publishing capability stub for Step 04-A.

Authority:
- ADR-022: Publish immutable version protocol (STEP-04C).
- TP-04A: Publish stub must fail closed with 503 capability_unavailable; no fabricated versions.
"""

from __future__ import annotations

from control_api.modules.agents.models.errors import AgentCapabilityUnavailableError


def publish_agent_revision(workspace_id: str, agent_id: str, actor_id: str) -> dict:
    """Fail-closed stub for 04-A.

    Publishing immutable versions requires the receipt table and binding guards
    designed in STEP-04C. In 04-A, this capability is explicitly disabled.
    """
    raise AgentCapabilityUnavailableError(
        "Agent publish capability is disabled in STEP-04A and will be activated in STEP-04C"
    )
