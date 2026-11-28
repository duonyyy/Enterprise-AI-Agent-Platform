"""Unit tests for RunEventService, RunEventRepository, projection, authority, and cursor validation (06-C)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agent_runtime.application.run_event_service import RunEventService
from agent_runtime.application.run_events import (
    MAX_FRAME_BYTES,
    RunEventCursorAheadError,
    RunEventFrameTooLargeError,
    RunEventHistoryInconsistentError,
    RunEventHistoryPrunedError,
    RunEventNotFoundError,
    map_row_to_run_event_dto,
)
from agent_runtime.auth import RuntimePrincipal


class _Mappings:
    def __init__(self, item: Any = None, items: list[Any] | None = None) -> None:
        self._item = item
        self._items = items or []

    def one_or_none(self) -> Any:
        return self._item

    def all(self) -> list[Any]:
        return self._items


def _make_principal(
    *,
    user_id: str,
    tenant_id: str,
    workspace_id: str,
    role: str = "developer",
    scopes: tuple[str, ...] = ("run:read", "run:start"),
) -> RuntimePrincipal:
    return RuntimePrincipal(
        subject=f"sub-{user_id}",
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        roles=(role,),
        scopes=frozenset(scopes),
        session_id=str(uuid4()),
        token_scopes=frozenset(scopes),
    )


def test_history_cursor_ahead_returns_409_conflict() -> None:
    """Cursor exceeding head sequence raises RunEventCursorAheadError (409)."""

    async def _test() -> None:
        tenant_id = str(uuid4())
        workspace_id = str(uuid4())
        user_id = str(uuid4())
        run_id = str(uuid4())

        principal = _make_principal(
            user_id=user_id, tenant_id=tenant_id, workspace_id=workspace_id, role="developer"
        )

        admission_repo = AsyncMock()
        admission_repo.verify_active_workspace_membership.return_value = "developer"

        event_repo = AsyncMock()
        # next_event_seq = 4 -> head = 3, oldest = 1
        event_repo.get_run_snapshot.return_value = {
            "head": 3,
            "oldest": 1,
            "prefix_floor": 0,
            "status": "running",
            "initiated_by": user_id,
            "trace_id": None,
        }

        @asynccontextmanager
        async def session_factory(t_id: str, w_id: str, u_id: str | None = None):
            yield AsyncMock()

        service = RunEventService(
            session_factory=session_factory,
            event_repository=event_repo,
            admission_repository=admission_repo,
        )

        # Request cursor 5 when head is 3 -> 409 cursor_ahead
        with pytest.raises(RunEventCursorAheadError) as exc:
            await service.get_history(principal=principal, run_id=run_id, last_event_id="5")
        assert exc.value.code == "cursor_ahead"

    import asyncio

    asyncio.run(_test())


def test_history_cursor_pruned_returns_410_gone_with_sequences() -> None:
    """Cursor preceding prefix_floor raises RunEventHistoryPrunedError (410) with oldest/head."""

    async def _test() -> None:
        tenant_id = str(uuid4())
        workspace_id = str(uuid4())
        user_id = str(uuid4())
        run_id = str(uuid4())

        principal = _make_principal(
            user_id=user_id, tenant_id=tenant_id, workspace_id=workspace_id, role="developer"
        )

        admission_repo = AsyncMock()
        admission_repo.verify_active_workspace_membership.return_value = "developer"

        event_repo = AsyncMock()
        # Rows 1..10 pruned, oldest = 11, head = 20 -> prefix_floor = 10
        event_repo.get_run_snapshot.return_value = {
            "head": 20,
            "oldest": 11,
            "prefix_floor": 10,
            "status": "running",
            "initiated_by": user_id,
            "trace_id": None,
        }

        @asynccontextmanager
        async def session_factory(t_id: str, w_id: str, u_id: str | None = None):
            yield AsyncMock()

        service = RunEventService(
            session_factory=session_factory,
            event_repository=event_repo,
            admission_repository=admission_repo,
        )

        # Request cursor 5 when prefix_floor is 10 -> 410 history_pruned
        with pytest.raises(RunEventHistoryPrunedError) as exc:
            await service.get_history(principal=principal, run_id=run_id, last_event_id="5")
        assert exc.value.code == "history_pruned"
        assert exc.value.oldest_available_sequence == 11
        assert exc.value.head_sequence == 20

        # Cursor exactly at prefix_floor (10) is valid!
        event_repo.fetch_events_batch.return_value = [
            {
                "id": str(uuid4()),
                "sequence": 11,
                "event_type": "run.step_started",
                "created_at": datetime.now(UTC),
                "projected_data": {"status": "running", "attempt": 1},
            }
        ]
        resp = await service.get_history(
            principal=principal, run_id=run_id, last_event_id="10", limit=1
        )
        assert resp.oldest_available_sequence == 11
        assert resp.head_sequence == 20
        assert len(resp.events) == 1
        assert resp.events[0].sequence == 11

    import asyncio

    asyncio.run(_test())


def test_history_internal_gap_detection_raises_inconsistent_503() -> None:
    """Detecting sequence holes in retained batch raises RunEventHistoryInconsistentError (503)."""

    async def _test() -> None:
        tenant_id = str(uuid4())
        workspace_id = str(uuid4())
        user_id = str(uuid4())
        run_id = str(uuid4())

        principal = _make_principal(
            user_id=user_id, tenant_id=tenant_id, workspace_id=workspace_id, role="developer"
        )

        admission_repo = AsyncMock()
        admission_repo.verify_active_workspace_membership.return_value = "developer"

        event_repo = AsyncMock()
        event_repo.get_run_snapshot.return_value = {
            "head": 5,
            "oldest": 1,
            "prefix_floor": 0,
            "status": "running",
            "initiated_by": user_id,
            "trace_id": None,
        }
        # Returned batch has sequence 1 and 3 (missing 2!)
        event_repo.fetch_events_batch.return_value = [
            {
                "id": str(uuid4()),
                "sequence": 1,
                "event_type": "run.queued",
                "created_at": datetime.now(UTC),
                "projected_data": {"status": "queued"},
            },
            {
                "id": str(uuid4()),
                "sequence": 3,
                "event_type": "run.started",
                "created_at": datetime.now(UTC),
                "projected_data": {"status": "running"},
            },
        ]

        @asynccontextmanager
        async def session_factory(t_id: str, w_id: str, u_id: str | None = None):
            yield AsyncMock()

        service = RunEventService(
            session_factory=session_factory,
            event_repository=event_repo,
            admission_repository=admission_repo,
        )

        with pytest.raises(RunEventHistoryInconsistentError) as exc:
            await service.get_history(principal=principal, run_id=run_id, last_event_id="0")
        assert exc.value.code == "event_history_inconsistent"

    import asyncio

    asyncio.run(_test())


def test_resource_authority_matrix_peer_run_hiding() -> None:
    """Unified resource authority: peer run is readable by Owner/Admin/Operator, hidden (404) from Developer/Viewer."""

    async def _test() -> None:
        tenant_id = str(uuid4())
        workspace_id = str(uuid4())
        creator_id = str(uuid4())
        peer_user_id = str(uuid4())
        run_id = str(uuid4())

        admission_repo = AsyncMock()
        event_repo = AsyncMock()
        event_repo.get_run_snapshot.return_value = {
            "head": 1,
            "oldest": 1,
            "prefix_floor": 0,
            "status": "running",
            "initiated_by": creator_id,
            "trace_id": None,
        }
        event_repo.fetch_events_batch.return_value = [
            {
                "id": str(uuid4()),
                "sequence": 1,
                "event_type": "run.queued",
                "created_at": datetime.now(UTC),
                "projected_data": {"status": "queued"},
            }
        ]

        @asynccontextmanager
        async def session_factory(t_id: str, w_id: str, u_id: str | None = None):
            yield AsyncMock()

        service = RunEventService(
            session_factory=session_factory,
            event_repository=event_repo,
            admission_repository=admission_repo,
        )

        # 1. Creator can read their own run regardless of role
        for role in ("developer", "viewer", "operator", "admin", "owner", "maintainer"):
            creator_principal = _make_principal(
                user_id=creator_id, tenant_id=tenant_id, workspace_id=workspace_id, role=role
            )
            admission_repo.verify_active_workspace_membership.return_value = role
            resp = await service.get_history(principal=creator_principal, run_id=run_id)
            assert resp.run_id == run_id

        # 2. Peer run reading:
        # Allowed: Owner, Admin, Operator
        for allowed_role in ("owner", "admin", "operator"):
            peer_principal = _make_principal(
                user_id=peer_user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                role=allowed_role,
            )
            admission_repo.verify_active_workspace_membership.return_value = allowed_role
            resp = await service.get_history(principal=peer_principal, run_id=run_id)
            assert resp.run_id == run_id

        # Denied (404 not found): Developer, Viewer, Maintainer
        for denied_role in ("developer", "viewer", "maintainer"):
            peer_principal = _make_principal(
                user_id=peer_user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                role=denied_role,
            )
            admission_repo.verify_active_workspace_membership.return_value = denied_role
            with pytest.raises(RunEventNotFoundError):
                await service.get_history(principal=peer_principal, run_id=run_id)

    import asyncio

    asyncio.run(_test())


def test_frame_size_cap_enforcement_raises_when_envelope_exceeds_16384_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frame cap check strictly rejects any event envelope exceeding 16384 bytes."""
    run_id = str(uuid4())
    workspace_id = str(uuid4())

    oversized_data = {
        "status": "queued",
        "availabilityReason": "no_executor_configured",
    }
    # Normal frame passes
    row = {
        "id": str(uuid4()),
        "sequence": 1,
        "event_type": "run.queued",
        "created_at": datetime.now(UTC),
        "projected_data": oversized_data,
    }
    dto = map_row_to_run_event_dto(row, workspace_id=workspace_id, run_id=run_id)
    assert len(dto.to_compact_json().encode("utf-8")) <= MAX_FRAME_BYTES

    # Artificially huge payload
    huge_data = {
        "status": "queued",
        "availabilityReason": "x" * 20000,
    }
    huge_row = {
        "id": str(uuid4()),
        "sequence": 1,
        "event_type": "run.queued",
        "created_at": datetime.now(UTC),
        "projected_data": huge_data,
    }
    # Invalid optional metadata is omitted, rather than forwarded until the cap trips.
    filtered = map_row_to_run_event_dto(huge_row, workspace_id=workspace_id, run_id=run_id)
    assert "availabilityReason" not in filtered.data
    monkeypatch.setattr("agent_runtime.application.run_events.MAX_FRAME_BYTES", 128)
    with pytest.raises(RunEventFrameTooLargeError):
        map_row_to_run_event_dto(huge_row, workspace_id=workspace_id, run_id=run_id)
