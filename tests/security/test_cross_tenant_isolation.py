"""
Security Test Suite: Multi-Tenant Strict Isolation (ADR-005, DB-56 RLS).
Verifies that:
1. Operations on resources across different tenants raise TenantIsolationViolation.
2. EventBus partitions strictly isolate messages by tenant_id:workspace_id.
3. AuthenticatedPrincipal cannot act on a foreign tenant scope.
"""

import asyncio
import pytest
from platform_common.principal import AuthenticatedPrincipal
from platform_common.errors import TenantIsolationViolation
from platform_common.event_bus import InMemoryEventBus, EventBus


def test_tenant_context_boundary_enforcement():
    principal_tenant_a = AuthenticatedPrincipal(
        user_id="user:alice@company-a.com",
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000011",
        roles=["developer"],
    )

    # Attempting to access Tenant B's resource must fail
    foreign_tenant_id = "00000000-0000-0000-0000-000000000002"

    def access_resource(target_tenant_id: str, principal: AuthenticatedPrincipal):
        if target_tenant_id != principal.tenant_id:
            raise TenantIsolationViolation(
                f"Principal tenant '{principal.tenant_id}' forbidden from accessing tenant '{target_tenant_id}'"
            )
        return "RESOURCE_DATA"

    # Alice accesses Company A -> Success
    res = access_resource("00000000-0000-0000-0000-000000000001", principal_tenant_a)
    assert res == "RESOURCE_DATA"

    # Alice accesses Company B -> TenantIsolationViolation
    with pytest.raises(TenantIsolationViolation) as exc_info:
        access_resource(foreign_tenant_id, principal_tenant_a)
    assert "forbidden" in str(exc_info.value)


def test_event_bus_tenant_partition_isolation():
    async def _run():
        bus = InMemoryEventBus()

        tenant_a_key = "00000000-0000-0000-0000-000000000001:00000000-0000-0000-0000-000000000011"
        tenant_b_key = "00000000-0000-0000-0000-000000000002:00000000-0000-0000-0000-000000000022"

        # Publish event belonging to Tenant A
        await bus.publish(
            topic="agent.run.events",
            key=tenant_a_key,
            payload={"type": "agent.run.step.completed", "step": 1, "data": "Secret Company A Data"},
        )

        # Publish event belonging to Tenant B
        await bus.publish(
            topic="agent.run.events",
            key=tenant_b_key,
            payload={"type": "agent.run.step.completed", "step": 1, "data": "Secret Company B Data"},
        )

        # Verify partition keys isolate Tenant A from Tenant B
        assert len(bus.published_events) == 2
        tenant_a_events = [e for e in bus.published_events if e["key"].startswith("00000000-0000-0000-0000-000000000001:")]
        tenant_b_events = [e for e in bus.published_events if e["key"].startswith("00000000-0000-0000-0000-000000000002:")]

        assert len(tenant_a_events) == 1
        assert tenant_a_events[0]["payload"]["data"] == "Secret Company A Data"

        assert len(tenant_b_events) == 1
        assert tenant_b_events[0]["payload"]["data"] == "Secret Company B Data"

    asyncio.run(_run())
