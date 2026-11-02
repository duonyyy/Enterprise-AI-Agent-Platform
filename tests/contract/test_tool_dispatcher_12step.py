"""Contract guardrails for the durable Tool Gateway."""

import pytest

from agent_runtime.tools import ToolDispatcher, ToolInvocation
from platform_common.principal import AuthenticatedPrincipal


def _call(arguments: dict) -> ToolInvocation:
    return ToolInvocation(
        principal=AuthenticatedPrincipal(
            user_id="00000000-0000-0000-0000-000000000101",
            tenant_id="00000000-0000-0000-0000-000000000001",
            workspace_id="00000000-0000-0000-0000-000000000011",
            roles=["developer"],
        ),
        run_id="00000000-0000-0000-0000-000000000201",
        agent_version_id="00000000-0000-0000-0000-000000000301",
        tool_version_id="00000000-0000-0000-0000-000000000401",
        operation_key="run-201:step-1",
        arguments=arguments,
        step_key="step-1",
    )


def test_dispatcher_requires_postgresql_session_factory():
    with pytest.raises(ValueError, match="PostgreSQL session factory"):
        ToolDispatcher(None)


def test_action_hash_is_bound_to_trusted_scope_and_arguments():
    first = ToolDispatcher.compute_action_hash(_call({"requestId": "a"}))
    second = ToolDispatcher.compute_action_hash(_call({"requestId": "a"}))
    altered = ToolDispatcher.compute_action_hash(_call({"requestId": "b"}))
    assert first == second
    assert first != altered
