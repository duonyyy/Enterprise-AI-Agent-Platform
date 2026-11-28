"""Scoped maintenance CLI for queued run expiry and read-only retention evaluation (06-D).

Entry point:
python -m agent_runtime.maintenance --task expire-queued --tenant-id <UUID> --workspace-id <UUID> --limit 100

DEC-06-CD constraints:
- Default dry-run. --apply requires run_maintenance_mutations_enabled=true in settings.
- Scoped to tenant_id and workspace_id under platform_app RLS context.
- System actor: "system:queue-expiry".
- Suspended workspace allows internal queue expiry, but context must exist.
- Retention-report is strictly read-only and rejects --apply.
- Exit code 0 on success/no candidate, 2 on input/config/apply-guard error, 1 on DB/row error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from platform_common.event_bus import PostgresOutboxEventBus
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from agent_runtime.application.run_data import RunDataStorageUnavailableError
from agent_runtime.application.run_lifecycle import (
    RunLifecycleConflictError,
    RunLifecycleError,
    RunLifecycleNotFoundError,
)
from agent_runtime.persistence.database import get_session_factory, scoped_session
from agent_runtime.persistence.object_store import create_object_store
from agent_runtime.persistence.run_event_repository import RunEventRepository
from agent_runtime.persistence.run_lifecycle_repository import RunLifecycleRepository
from agent_runtime.persistence.run_retention_repository import RunRetentionRepository
from agent_runtime.settings import get_settings

logger = logging.getLogger(__name__)

MAX_MAINTENANCE_TIMEOUT_SECONDS = 30.0


def _orphan_disposition(
    *, now: datetime, last_modified: datetime, referenced: bool
) -> Literal["protected", "unknown"]:
    if referenced or now < last_modified + timedelta(hours=24):
        return "protected"
    # There is no approved complete checkpoint-object / ambiguous-commit inventory yet.
    return "unknown"


def _remaining_budget(start_time: float) -> float:
    remaining = MAX_MAINTENANCE_TIMEOUT_SECONDS - (time.monotonic() - start_time)
    if remaining <= 0:
        raise TimeoutError("Maintenance deadline exceeded")
    return remaining


def _is_lock_conflict(exc: SQLAlchemyError) -> bool:
    original = getattr(exc, "orig", None)
    return (
        getattr(original, "sqlstate", None) == "55P03"
        or getattr(getattr(original, "__cause__", None), "sqlstate", None) == "55P03"
    )


async def _run_expire_queued(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int,
    apply: bool,
    start_time: float,
) -> dict[str, Any]:
    """Execute queue expiry sweep (dry-run by default, atomic mutations if --apply)."""
    UUID(tenant_id)
    UUID(workspace_id)

    outbox = PostgresOutboxEventBus(lambda: get_session_factory()())
    event_repo = RunEventRepository()
    lifecycle_repo = RunLifecycleRepository(
        event_repository=event_repo,
        outbox_publisher=outbox,
    )

    # 1. Verify workspace exists and lock FOR SHARE (internal maintenance allows suspended workspaces)
    async with (
        asyncio.timeout(_remaining_budget(start_time)),
        scoped_session(tenant_id, workspace_id) as session,
    ):
        await session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
        await lifecycle_repo.verify_scope_and_lock_share(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            is_system_maintenance=True,
        )
        candidates = await lifecycle_repo.get_candidate_expired_runs(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            limit=limit,
        )

    scanned = len(candidates)
    eligible = 0
    unsupported = 0
    changed = 0
    failed = 0
    skipped_locked = 0
    protected = 0

    for item in candidates:
        if time.monotonic() - start_time > MAX_MAINTENANCE_TIMEOUT_SECONDS:
            failed += 1
            break

        run_id_str = str(item["id"])
        # Legacy runs lacking verified admission receipts are unsupported for auto-mutation
        if item.get("request_sha256") is None:
            unsupported += 1
            continue

        # In apply mode: each run executes in an isolated short transaction
        try:
            async with (
                asyncio.timeout(_remaining_budget(start_time)),
                scoped_session(tenant_id, workspace_id) as run_session,
            ):
                await run_session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
                await run_session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                res = await lifecycle_repo.cancel_or_expire_run(
                    run_session,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    run_id=run_id_str,
                    actor_subject="system:queue-expiry",
                    is_system_maintenance=True,
                    skip_locked=True,
                    dry_run=not apply,
                )
                if res.disposition == "already_terminal":
                    protected += 1
                else:
                    eligible += 1
                if res.changed:
                    changed += 1
        except RunLifecycleConflictError as exc:
            if exc.code == "row_locked":
                skipped_locked += 1
            elif exc.code == "legacy_run_unsupported":
                unsupported += 1
            else:
                protected += 1
        except SQLAlchemyError as exc:
            if _is_lock_conflict(exc):
                skipped_locked += 1
            else:
                logger.error("Queue expiry database failure (%s)", type(exc).__name__)
                failed += 1
        except TimeoutError:
            failed += 1
            break
        except (RunLifecycleError, OSError) as exc:
            logger.error("Queue expiry failure (%s)", type(exc).__name__)
            failed += 1

    elapsed = time.monotonic() - start_time
    return {
        "task": "expire-queued",
        "dry_run": not apply,
        "scanned": scanned,
        "eligible": eligible,
        "changed": changed,
        "skipped_locked": skipped_locked,
        "protected": protected,
        "unsupported": unsupported,
        "failed": failed,
        "elapsed_seconds": round(elapsed, 4),
    }


async def _run_retention_report(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int,
    start_time: float,
) -> dict[str, Any]:
    """Execute read-only retention evaluator (content 30d, timeline 90d, legal hold; zero mutations)."""
    UUID(tenant_id)
    UUID(workspace_id)
    repo = RunRetentionRepository()
    scope_repo = RunLifecycleRepository()
    async with (
        asyncio.timeout(_remaining_budget(start_time)),
        scoped_session(tenant_id, workspace_id) as session,
    ):
        await session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
        await scope_repo.verify_scope_and_lock_share(
            session, tenant_id=tenant_id, workspace_id=workspace_id, is_system_maintenance=True
        )
        assessment = await repo.evaluate(
            session, tenant_id=tenant_id, workspace_id=workspace_id, limit=limit
        )
        now = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()

    # S3 IO is outside DB transactions. Missing inventory never means no orphans.
    orphan: dict[str, Any] = {
        "status": "unknown",
        "scanned": 0,
        "eligible": 0,
        "protected": 0,
        "unknown": 0,
        "grace_seconds": 86400,
        "checkpoint_inventory_complete": False,
        "has_more": False,
    }
    store = create_object_store(get_settings())
    try:
        async with asyncio.timeout(min(5.0, _remaining_budget(start_time))):
            page = await store.list_scoped_run_objects(
                tenant_id=tenant_id, workspace_id=workspace_id, limit=limit
            )
        orphan["status"] = "assessed_with_unknown_references"
        orphan["has_more"] = page.truncated
        for item in page.objects:
            orphan["scanned"] += 1
            if (
                _orphan_disposition(now=now, last_modified=item.last_modified, referenced=False)
                == "protected"
            ):
                orphan["protected"] += 1
                continue
            ref = f"s3://{store.default_bucket}/{item.key}"
            async with (
                asyncio.timeout(_remaining_budget(start_time)),
                scoped_session(tenant_id, workspace_id) as session,
            ):
                await session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
                referenced = await repo.object_is_referenced(
                    session, tenant_id=tenant_id, workspace_id=workspace_id, ref=ref
                )
            orphan[
                _orphan_disposition(
                    now=now, last_modified=item.last_modified, referenced=referenced
                )
            ] += 1
    except (RunDataStorageUnavailableError, TimeoutError, OSError):
        orphan["status"] = "inventory_unavailable"
    finally:
        await store.aclose()
    return {
        "task": "retention-report",
        "dry_run": True,
        **assessment,
        "orphans": orphan,
        "changed": 0,
        "skipped_locked": 0,
        "unsupported": 0,
        "failed": 0,
        "elapsed_seconds": round(time.monotonic() - start_time, 4),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for scoped maintenance."""
    parser = argparse.ArgumentParser(description="Scoped maintenance CLI for runtime lifecycle.")
    parser.add_argument(
        "--task",
        choices=["expire-queued", "retention-report"],
        required=True,
        help="Maintenance task to run",
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant UUID")
    parser.add_argument("--workspace-id", required=True, help="Workspace UUID")
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum items to scan (1..100)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply mutations (requires run_maintenance_mutations_enabled=true)",
    )

    args = parser.parse_args(argv)

    # 1. Validate inputs
    try:
        tenant_id = str(UUID(args.tenant_id))
        workspace_id = str(UUID(args.workspace_id))
    except (ValueError, TypeError):
        print("ERROR: Invalid tenant_id or workspace_id UUID", file=sys.stderr)
        return 2

    if args.limit < 1 or args.limit > 100:
        print("ERROR: limit must be between 1 and 100", file=sys.stderr)
        return 2

    settings = get_settings()

    # 2. Validate task-specific guards
    if args.task == "retention-report" and args.apply:
        print(
            "ERROR: retention-report is strictly read-only and does not accept --apply",
            file=sys.stderr,
        )
        return 2

    if (
        args.task == "expire-queued"
        and args.apply
        and not settings.run_maintenance_mutations_enabled
    ):
        print(
            "ERROR: --apply is forbidden when run_maintenance_mutations_enabled=false",
            file=sys.stderr,
        )
        return 2

    start_time = time.monotonic()

    # 3. Execute task
    try:
        if args.task == "expire-queued":
            summary = asyncio.run(
                _run_expire_queued(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    limit=args.limit,
                    apply=args.apply,
                    start_time=start_time,
                )
            )
        else:
            summary = asyncio.run(
                _run_retention_report(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    limit=args.limit,
                    start_time=start_time,
                )
            )
    except RunLifecycleNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Maintenance failed ({type(exc).__name__})", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2))
    if summary["failed"] > 0 or summary["elapsed_seconds"] > MAX_MAINTENANCE_TIMEOUT_SECONDS:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
