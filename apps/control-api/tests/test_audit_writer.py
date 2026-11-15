"""Unit and contract tests for shared transactional audit writer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from control_api.core.audit import AuditEventWrite, write_audit_event


@pytest.mark.anyio
async def test_write_audit_event_executes_insert_and_returns_id():
    session = AsyncMock()
    audit_id = uuid4()
    mock_result = MagicMock()
    mock_result.mappings.return_value.one.return_value = {"id": audit_id}
    session.execute.return_value = mock_result

    tenant_id = uuid4()
    workspace_id = uuid4()
    actor_id = uuid4()
    resource_id = uuid4()

    event = AuditEventWrite(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_subject=str(actor_id),
        action="workspace.invitation.created",
        resource_type="workspace_invitation",
        resource_id=str(resource_id),
        outcome="succeeded",
        request_id="req-123",
        trace_id="tr-456",
        reason_code=None,
        details_redacted={"recipient": "user@example.com", "role": "developer"},
    )

    returned_id = await write_audit_event(session, event)

    assert returned_id == str(audit_id)
    session.execute.assert_awaited_once()

    call_args = session.execute.await_args
    params = call_args.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["workspace_id"] == workspace_id
    assert params["actor_subject"] == str(actor_id)
    assert params["action"] == "workspace.invitation.created"
    assert params["resource_type"] == "workspace_invitation"
    assert params["resource_id"] == str(resource_id)
    assert params["outcome"] == "succeeded"
    assert params["request_id"] == "req-123"
    assert params["trace_id"] == "tr-456"
    assert params["reason_code"] is None

    # Redacted details are serialized as sorted JSON
    parsed_details = json.loads(params["details_redacted"])
    assert parsed_details == {"recipient": "user@example.com", "role": "developer"}

    # Transaction lifecycle is caller's responsibility: writer does not commit or rollback
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.anyio
async def test_write_audit_event_preserves_reason_code_when_present():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.one.return_value = {"id": uuid4()}
    session.execute.return_value = mock_result

    event = AuditEventWrite(
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        actor_subject="admin",
        action="membership.role_changed",
        resource_type="membership",
        resource_id=str(uuid4()),
        reason_code="membership.role_changed",
    )

    await write_audit_event(session, event)
    params = session.execute.await_args.args[1]
    assert params["reason_code"] == "membership.role_changed"


@pytest.mark.anyio
async def test_write_audit_event_propagates_exceptions_without_swallowing():
    session = AsyncMock()
    session.execute.side_effect = RuntimeError("DB connection failure")

    event = AuditEventWrite(
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        actor_subject="admin",
        action="test.action",
        resource_type="test",
        resource_id=str(uuid4()),
    )

    with pytest.raises(RuntimeError, match="DB connection failure"):
        await write_audit_event(session, event)
