"""Contract and unit verification for shared PostgreSQL RLS context helper."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from platform_common.rls import set_rls_context


@pytest.mark.anyio
async def test_set_rls_context_binds_all_three_variables_with_parameterized_query():
    session = AsyncMock()
    tenant_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()

    await set_rls_context(
        session,
        tenant_id=str(tenant_id),
        workspace_id=str(workspace_id),
        user_id=str(user_id),
    )

    session.execute.assert_awaited_once()
    call_args = session.execute.await_args
    sql_text = str(call_args.args[0])
    params = call_args.args[1]

    assert "set_config('app.tenant_id', :tenant_id, true)" in sql_text
    assert "set_config('app.workspace_id', :workspace_id, true)" in sql_text
    assert "set_config('app.user_id', :user_id, true)" in sql_text

    assert params["tenant_id"] == str(tenant_id)
    assert params["workspace_id"] == str(workspace_id)
    assert params["user_id"] == str(user_id)


@pytest.mark.anyio
async def test_set_rls_context_resets_user_id_to_empty_string_when_none():
    session = AsyncMock()
    tenant_id = uuid4()
    workspace_id = uuid4()

    await set_rls_context(
        session,
        tenant_id=str(tenant_id),
        workspace_id=str(workspace_id),
        user_id=None,
    )

    session.execute.assert_awaited_once()
    params = session.execute.await_args.args[1]

    # Explicit empty string prevents lingering actor context on pooled connections
    assert params["user_id"] == ""
    assert params["tenant_id"] == str(tenant_id)
    assert params["workspace_id"] == str(workspace_id)


def test_set_rls_context_re_exported_from_control_api_and_runtime():
    from agent_runtime.persistence.database import set_rls_context as runtime_set_rls
    from control_api.core.database import set_rls_context as control_api_set_rls
    from platform_common import set_rls_context as common_set_rls

    assert control_api_set_rls is set_rls_context
    assert runtime_set_rls is set_rls_context
    assert common_set_rls is set_rls_context
