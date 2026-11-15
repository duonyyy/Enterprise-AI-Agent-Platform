"""Shared PostgreSQL Row-Level Security (RLS) context helpers.

Authority:
- ADR-005 (Trust boundaries): Authenticated principal and tenant/workspace scope come from
  trusted server-side state, strictly enforced by PostgreSQL RLS.
- DEC-DB-01: Fail-closed tenant & workspace isolation via PostgreSQL session settings.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_rls_context(
    session: AsyncSession,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None = None,
) -> None:
    """Bind transaction-local RLS context (`is_local = true`) for the active transaction.

    This binds:
    - ``app.tenant_id`` = tenant_id
    - ``app.workspace_id`` = workspace_id
    - ``app.user_id`` = user_id or "" (explicitly clears any prior user context if None)

    All settings use ``is_local = true`` ensuring PostgreSQL automatically reverts them
    at the end of the transaction (COMMIT or ROLLBACK).
    Does NOT commit, rollback, or alter the caller's transaction lifecycle.
    """
    await session.execute(
        text(
            "SELECT "
            "set_config('app.tenant_id', :tenant_id, true), "
            "set_config('app.workspace_id', :workspace_id, true), "
            "set_config('app.user_id', :user_id, true)"
        ),
        {
            "tenant_id": str(tenant_id),
            "workspace_id": str(workspace_id),
            "user_id": str(user_id) if user_id else "",
        },
    )


__all__ = ["set_rls_context"]
