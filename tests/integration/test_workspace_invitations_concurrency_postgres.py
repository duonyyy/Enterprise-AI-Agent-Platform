"""Real PostgreSQL 16 concurrency and lock-order regression tests for Step 03-B R01."""

from __future__ import annotations

import asyncio
import email as email_stdlib
import re
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.workspaces.repositories.invitation_repository import (
    InvitationPolicyConfig,
    InvitationRepository,
)
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from jobs_worker.invitation_consumer import InvitationOutboxConsumer, _extract_sqlstate
from jobs_worker.smtp_adapter import SmtpConfig, SmtpSendResult, StdlibSmtpTransport
from platform_common.event_bus import PostgresOutboxEventBus
from platform_common.invitations import InvitationProofProtector, hash_invitation_proof
from run_migrations import run_migrations
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.test_workspace_invitations_real_postgres_smtp import (
    _configure_control_api_for_db,
    _issue_session_cookie_header,
    _RealSmtpSinkServer,
    _seed_workspace_and_users,
    _to_async_pg_url,
    _to_sync_pg_url,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "database/migrations"


@contextmanager
def _open_consumer(
    app_url: str,
    seed: dict[str, Any],
    protector: InvitationProofProtector,
    smtp_port: int,
    *,
    engine: Engine | None = None,
    retry_delays: tuple[int, ...] = (30, 120),
    max_attempts: int = 3,
    db_retry_max_attempts: int = 3,
) -> Iterator[InvitationOutboxConsumer]:
    owned_engine = engine is None
    db_engine = engine or create_engine(_to_sync_pg_url(app_url), pool_pre_ping=True)
    try:
        yield InvitationOutboxConsumer(
            database_url=_to_sync_pg_url(app_url),
            tenant_id=str(seed["tenant_a"]),
            workspace_id=str(seed["ws_a"]),
            protector=protector,
            public_app_origin="http://localhost:3000",
            smtp_transport=StdlibSmtpTransport(
                SmtpConfig(
                    host="127.0.0.1",
                    port=smtp_port,
                    sender="no-reply@example.com",
                    tls_mode="none",
                    allow_plaintext_local_smtp=True,
                )
            ),
            retry_delays_seconds=retry_delays,
            max_attempts=max_attempts,
            db_retry_max_attempts=db_retry_max_attempts,
            db_retry_backoff_seconds=(0.01, 0.02),
            engine=db_engine,
        )
    finally:
        if owned_engine:
            db_engine.dispose()


async def _repo_create_invitation(
    app_url: str,
    seed: dict[str, Any],
    protector: InvitationProofProtector,
    email_addr: str,
    *,
    role: str = "viewer",
) -> dict[str, Any]:
    engine = create_async_engine(_to_async_pg_url(app_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = InvitationRepository(PostgresOutboxEventBus(factory))
    try:
        async with factory() as session, session.begin():
            return await repo.create_invitation(
                session,
                tenant_id=seed["tenant_a"],
                workspace_id=seed["ws_a"],
                actor_user_id=seed["owner_a"],
                recipient_email_normalized=email_addr,
                recipient_issuer=seed["issuer"],
                role=role,
                protector=protector,
                policy=InvitationPolicyConfig(72, 0, 50, 100),
                request_id=f"req-{uuid4().hex[:8]}",
                trace_id=f"trace-{uuid4().hex[:8]}",
            )
    finally:
        await engine.dispose()


def _extract_proof_token_from_raw_mime(raw: str) -> str:
    msg = email_stdlib.message_from_string(raw)
    body = msg.get_payload(decode=True).decode("utf-8")
    match = re.search(r"#token=([A-Za-z0-9\-_]+)", body)
    assert match is not None, f"Missing invite proof fragment in email body: {body}"
    return match.group(1)


@pytest.mark.integration
def test_concurrent_revoke_and_worker_claim_no_deadlock_both_directions(
    isolated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    """R01 regression: concurrent API revoke and worker claim never deadlock (40P01 / 503) in either order."""
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]
    run_migrations(owner_url, MIGRATIONS_DIR)
    seed = _seed_workspace_and_users(owner_url)
    fernet_key = Fernet.generate_key().decode("ascii")
    protector = InvitationProofProtector(fernet_key, "v1")

    # Direction A: Worker acquires workspace FOR SHARE, pauses before locking invitation/outbox,
    # while real HTTP POST /revoke awaits workspace lock. Revoke commits before dispatch check,
    # so worker sees revoked state at pre-dispatch check and does not send SMTP.
    sink_a = _RealSmtpSinkServer("accept")
    try:
        inv_a = asyncio.run(_repo_create_invitation(app_url, seed, protector, "dir-a@example.com"))
        inv_a_id = str(inv_a["invitation_id"])

        worker_locked_ws = Event()
        release_worker = Event()
        api_entered_ws = Event()
        hook_armed = True
        worker_engine = create_engine(_to_sync_pg_url(app_url), pool_pre_ping=True)

        @event.listens_for(worker_engine, "after_cursor_execute")
        def _pause_worker_after_ws(_conn, _cursor, statement, _params, _ctx, _many):
            if hook_armed and "FOR SHARE OF w" in statement:
                worker_locked_ws.set()
                assert release_worker.wait(10.0), "Timed out waiting to release worker"

        async def _http_revoke(inv_id: str) -> tuple[int, dict[str, Any]]:
            async_engine, _ = _configure_control_api_for_db(monkeypatch, app_url, fernet_key, [])

            @event.listens_for(async_engine.sync_engine, "before_cursor_execute")
            def _observe_api_ws(_conn, _cursor, statement, _params, _ctx, _many):
                if "FOR UPDATE OF w" in statement:
                    api_entered_ws.set()

            try:
                owner_headers = _issue_session_cookie_header(
                    seed["owner_a"], seed["issuer"], f"owner-{seed['owner_a']}"
                )
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://localhost:8000"
                ) as client:
                    resp = await client.post(
                        f"/v1/workspaces/{seed['ws_a']}/invitations/{inv_id}/revoke",
                        headers=owner_headers,
                    )
                    return resp.status_code, resp.json()
            finally:
                await async_engine.dispose()

        with _open_consumer(
            app_url, seed, protector, sink_a.port, engine=worker_engine
        ) as consumer_a:
            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    w_future = executor.submit(consumer_a._claim_next_outbox_delivery)
                    assert worker_locked_ws.wait(10.0)
                    a_future = executor.submit(lambda: asyncio.run(_http_revoke(inv_a_id)))
                    assert api_entered_ws.wait(10.0)
                    release_worker.set()
                    hook_armed = False
                    _had_a, claim_a = w_future.result(timeout=15.0)
                    status_a, body_a = a_future.result(timeout=15.0)
            finally:
                release_worker.set()

            assert status_a == 200, body_a
            assert body_a["status"] == "revoked"
            assert body_a["delivery_state"] == "cancelled"

            if claim_a is not None:
                # Since revoke committed before pre-dispatch check, worker must abort SMTP send.
                consumer_a._dispatch_claimed_delivery(claim_a)

        worker_engine.dispose()
        assert len(sink_a.messages) == 0
    finally:
        sink_a.close()

    # Direction B: API revoke holds workspace + invitation and pauses right before outbox UPDATE,
    # while worker concurrently enters _claim_next_outbox_delivery().
    sink_b = _RealSmtpSinkServer("accept")
    try:
        inv_b = asyncio.run(_repo_create_invitation(app_url, seed, protector, "dir-b@example.com"))
        inv_b_id = str(inv_b["invitation_id"])

        api_paused_before_outbox = Event()
        release_api = Event()
        worker_reached_ws_lock = Event()
        worker_engine_b = create_engine(_to_sync_pg_url(app_url), pool_pre_ping=True)

        @event.listens_for(worker_engine_b, "before_cursor_execute")
        def _observe_worker_ws(_conn, _cursor, statement, _params, _ctx, _many):
            if "FOR SHARE OF w" in statement:
                worker_reached_ws_lock.set()

        async def _repo_revoke_b() -> None:
            async_engine = create_async_engine(_to_async_pg_url(app_url))
            factory = async_sessionmaker(async_engine, expire_on_commit=False)
            repo = InvitationRepository(PostgresOutboxEventBus(factory))

            @event.listens_for(async_engine.sync_engine, "before_cursor_execute")
            def _pause_before_outbox(_conn, _cursor, statement, _params, _ctx, _many):
                if statement.startswith("UPDATE integration.outbox_events"):
                    api_paused_before_outbox.set()
                    assert release_api.wait(10.0), "Timed out waiting to release API"

            try:
                async with factory() as session, session.begin():
                    await repo.revoke_invitation(
                        session,
                        tenant_id=seed["tenant_a"],
                        workspace_id=seed["ws_a"],
                        invitation_id=UUID(inv_b_id),
                        actor_user_id=seed["owner_a"],
                        request_id="req-dir-b",
                        trace_id="trace-dir-b",
                    )
            finally:
                await async_engine.dispose()

        with _open_consumer(
            app_url, seed, protector, sink_b.port, engine=worker_engine_b
        ) as consumer_b:
            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    api_future = executor.submit(lambda: asyncio.run(_repo_revoke_b()))
                    assert api_paused_before_outbox.wait(10.0)
                    worker_future = executor.submit(consumer_b.process_next_event)
                    assert worker_reached_ws_lock.wait(10.0)
                    release_api.set()
                    api_future.result(timeout=15.0)
                    handled_b = worker_future.result(timeout=15.0)
            finally:
                release_api.set()
                worker_engine_b.dispose()

        assert handled_b is False
        assert len(sink_b.messages) == 0

        with psycopg.connect(owner_url) as conn:
            for iid in (UUID(inv_a_id), UUID(inv_b_id)):
                inv_row = conn.execute(
                    "SELECT status, delivery_state, proof_ciphertext, lease_token "
                    "FROM iam.workspace_invitations WHERE id = %s",
                    (iid,),
                ).fetchone()
                assert inv_row == ("revoked", "cancelled", None, None)
                ob_row = conn.execute(
                    "SELECT state FROM integration.outbox_events WHERE aggregate_id = %s",
                    (iid,),
                ).fetchone()
                assert ob_row == ("dead",)
    finally:
        sink_b.close()


@pytest.mark.integration
def test_concurrent_resend_and_worker_claim_generation_and_proof_integrity(
    isolated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    """Concurrent API resend + worker claim produces valid generation=2 delivery without 40P01/503."""
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]
    run_migrations(owner_url, MIGRATIONS_DIR)
    seed = _seed_workspace_and_users(owner_url)
    fernet_key = Fernet.generate_key().decode("ascii")
    protector = InvitationProofProtector(fernet_key, "v1")
    sink = _RealSmtpSinkServer("accept")

    try:
        inv = asyncio.run(
            _repo_create_invitation(app_url, seed, protector, "resend-race@example.com")
        )
        inv_id = str(inv["invitation_id"])
        gen1_outbox_id = UUID(inv["outbox_event_id"])

        api_before_outbox = Event()
        release_api = Event()
        worker_entered_ws = Event()
        worker_engine = create_engine(_to_sync_pg_url(app_url), pool_pre_ping=True)

        @event.listens_for(worker_engine, "before_cursor_execute")
        def _observe_worker_ws(_conn, _cursor, statement, _params, _ctx, _many):
            if "FOR SHARE OF w" in statement:
                worker_entered_ws.set()

        async def _http_resend() -> tuple[int, dict[str, Any]]:
            async_engine, _ = _configure_control_api_for_db(monkeypatch, app_url, fernet_key, [])
            monkeypatch.setattr(settings, "invitation_resend_cooldown_seconds", 0)

            @event.listens_for(async_engine.sync_engine, "before_cursor_execute")
            def _pause_api_before_outbox(_conn, _cursor, statement, _params, _ctx, _many):
                if statement.startswith("UPDATE integration.outbox_events"):
                    api_before_outbox.set()
                    assert release_api.wait(10.0), "Timed out releasing resend API"

            try:
                owner_headers = _issue_session_cookie_header(
                    seed["owner_a"], seed["issuer"], f"owner-{seed['owner_a']}"
                )
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://localhost:8000"
                ) as client:
                    resp = await client.post(
                        f"/v1/workspaces/{seed['ws_a']}/invitations/{inv_id}/resend",
                        headers=owner_headers,
                    )
                    return resp.status_code, resp.json()
            finally:
                await async_engine.dispose()

        with _open_consumer(app_url, seed, protector, sink.port, engine=worker_engine) as consumer:
            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    resend_future = executor.submit(lambda: asyncio.run(_http_resend()))
                    if not api_before_outbox.wait(10.0):
                        if resend_future.done():
                            raise AssertionError(
                                f"Resend exited before outbox update: {resend_future.result()}"
                            )
                        raise AssertionError(
                            "Timed out waiting for resend API to reach outbox update"
                        )
                    worker_future = executor.submit(consumer.run_once)
                    assert worker_entered_ws.wait(10.0)
                    release_api.set()
                    status_code, body = resend_future.result(timeout=15.0)
                    processed = worker_future.result(timeout=15.0)
            finally:
                release_api.set()
                worker_engine.dispose()

        assert status_code == 202, body
        assert processed >= 1
        assert len(sink.messages) == 1

        token_sent = _extract_proof_token_from_raw_mime(sink.messages[0]["raw"])
        with psycopg.connect(owner_url) as conn:
            inv_row = conn.execute(
                "SELECT status, generation, delivery_state, proof_ciphertext, token_hash "
                "FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_id),),
            ).fetchone()
            assert inv_row is not None
            assert inv_row[0] == "pending"
            assert inv_row[1] == 2
            assert inv_row[2] == "provider_accepted"
            assert inv_row[3] is None
            assert hash_invitation_proof(token_sent) == inv_row[4]

            gen1_state = conn.execute(
                "SELECT state FROM integration.outbox_events WHERE id = %s",
                (gen1_outbox_id,),
            ).fetchone()
            assert gen1_state == ("dead",)
    finally:
        sink.close()


@pytest.mark.integration
def test_concurrent_reclaim_expired_leases_with_claim_revoke_and_resend(
    isolated_database: dict[str, Any],
):
    """Cleanup (reclaim_expired_leases) running concurrently with claim, revoke, and resend stays deadlock-free."""
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]
    run_migrations(owner_url, MIGRATIONS_DIR)
    seed = _seed_workspace_and_users(owner_url)
    protector = InvitationProofProtector(Fernet.generate_key().decode("ascii"), "v1")
    sink = _RealSmtpSinkServer("accept")

    try:
        inv_lease = asyncio.run(
            _repo_create_invitation(app_url, seed, protector, "lease-exp@example.com")
        )
        inv_exp = asyncio.run(
            _repo_create_invitation(app_url, seed, protector, "pending-exp@example.com")
        )
        inv_revoke = asyncio.run(
            _repo_create_invitation(app_url, seed, protector, "live-revoke@example.com")
        )
        inv_resend = asyncio.run(
            _repo_create_invitation(app_url, seed, protector, "live-resend@example.com")
        )
        inv_active = asyncio.run(
            _repo_create_invitation(app_url, seed, protector, "live-send@example.com")
        )

        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "UPDATE iam.workspace_invitations "
                "SET delivery_state = 'sending', lease_token = %s, leased_until = now() - interval '1 minute' "
                "WHERE id = %s",
                (uuid4(), UUID(inv_lease["invitation_id"])),
            )
            conn.execute(
                "UPDATE integration.outbox_events SET state = 'delivering' WHERE id = %s",
                (UUID(inv_lease["outbox_event_id"]),),
            )
            conn.execute(
                "UPDATE iam.workspace_invitations "
                "SET created_at = now() - interval '2 days', expires_at = now() - interval '1 minute' "
                "WHERE id = %s",
                (UUID(inv_exp["invitation_id"]),),
            )

        with (
            _open_consumer(app_url, seed, protector, sink.port) as c_reclaim,
            _open_consumer(app_url, seed, protector, sink.port) as c_worker,
        ):
            start_barrier = Barrier(4)

            def _do_reclaim() -> int:
                start_barrier.wait(timeout=10.0)
                return c_reclaim.reclaim_expired_leases()

            def _do_worker_events() -> int:
                start_barrier.wait(timeout=10.0)
                handled = 0
                while c_worker.process_next_event():
                    handled += 1
                return handled

            async def _do_revoke() -> dict[str, Any]:
                engine = create_async_engine(_to_async_pg_url(app_url))
                factory = async_sessionmaker(engine, expire_on_commit=False)
                repo = InvitationRepository(PostgresOutboxEventBus(factory))
                try:
                    async with factory() as session, session.begin():
                        return await repo.revoke_invitation(
                            session,
                            tenant_id=seed["tenant_a"],
                            workspace_id=seed["ws_a"],
                            invitation_id=UUID(inv_revoke["invitation_id"]),
                            actor_user_id=seed["owner_a"],
                            request_id="req-conc-rev",
                            trace_id="trace-conc-rev",
                        )
                finally:
                    await engine.dispose()

            async def _do_resend() -> dict[str, Any]:
                engine = create_async_engine(_to_async_pg_url(app_url))
                factory = async_sessionmaker(engine, expire_on_commit=False)
                repo = InvitationRepository(PostgresOutboxEventBus(factory))
                try:
                    async with factory() as session, session.begin():
                        return await repo.resend_invitation(
                            session,
                            tenant_id=seed["tenant_a"],
                            workspace_id=seed["ws_a"],
                            invitation_id=UUID(inv_resend["invitation_id"]),
                            actor_user_id=seed["owner_a"],
                            protector=protector,
                            policy=InvitationPolicyConfig(72, 0, 50, 100),
                            request_id="req-conc-res",
                            trace_id="trace-conc-res",
                        )
                finally:
                    await engine.dispose()

            def _run_revoke() -> dict[str, Any]:
                start_barrier.wait(timeout=10.0)
                return asyncio.run(_do_revoke())

            def _run_resend() -> dict[str, Any]:
                start_barrier.wait(timeout=10.0)
                return asyncio.run(_do_resend())

            with ThreadPoolExecutor(max_workers=4) as executor:
                f_reclaim = executor.submit(_do_reclaim)
                f_worker = executor.submit(_do_worker_events)
                f_revoke = executor.submit(_run_revoke)
                f_resend = executor.submit(_run_resend)

                reclaimed = f_reclaim.result(timeout=20.0)
                _ = f_worker.result(timeout=20.0)
                revoke_res = f_revoke.result(timeout=20.0)
                resend_res = f_resend.result(timeout=20.0)

            # Drain any pending gen=2 event queued after worker finished initial loop
            c_worker.run_once()

        assert reclaimed == 1
        assert revoke_res["status"] == "revoked"
        assert resend_res["generation"] == 2

        with psycopg.connect(owner_url) as conn:
            assert conn.execute(
                "SELECT delivery_state, delivery_error_code, proof_ciphertext, lease_token "
                "FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_lease["invitation_id"]),),
            ).fetchone() == ("unknown", "LEASE_EXPIRED_UNKNOWN", None, None)

            assert conn.execute(
                "SELECT status, delivery_state, proof_ciphertext "
                "FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_exp["invitation_id"]),),
            ).fetchone() == ("expired", "cancelled", None)

            assert conn.execute(
                "SELECT status, delivery_state, proof_ciphertext "
                "FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_revoke["invitation_id"]),),
            ).fetchone() == ("revoked", "cancelled", None)

            assert conn.execute(
                "SELECT status, generation, delivery_state, proof_ciphertext "
                "FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_resend["invitation_id"]),),
            ).fetchone() == ("pending", 2, "provider_accepted", None)

            assert conn.execute(
                "SELECT status, delivery_state, proof_ciphertext "
                "FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_active["invitation_id"]),),
            ).fetchone() == ("pending", "provider_accepted", None)
    finally:
        sink.close()


@pytest.mark.integration
def test_concurrent_completion_vs_revoke_and_resend_fencing_and_inbox_fk_lock(
    isolated_database: dict[str, Any],
):
    """CAS completion holding workspace lock never deadlocks on inbox_receipts FK and never clobbers revoke/resend."""
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]
    run_migrations(owner_url, MIGRATIONS_DIR)
    seed = _seed_workspace_and_users(owner_url)
    protector = InvitationProofProtector(Fernet.generate_key().decode("ascii"), "v1")
    sink = _RealSmtpSinkServer("accept")

    try:
        # Part A: Pause worker completion after updating inv_1, right before INSERT INTO inbox_receipts,
        # while concurrent API revokes inv_2 in the SAME workspace.
        inv_1 = asyncio.run(_repo_create_invitation(app_url, seed, protector, "comp-1@example.com"))
        inv_2 = asyncio.run(_repo_create_invitation(app_url, seed, protector, "comp-2@example.com"))

        before_inbox_insert = Event()
        release_worker = Event()
        api_entered_ws = Event()
        worker_engine = create_engine(_to_sync_pg_url(app_url), pool_pre_ping=True)

        @event.listens_for(worker_engine, "before_cursor_execute")
        def _pause_before_inbox_insert(_conn, _cursor, statement, _params, _ctx, _many):
            if "INSERT INTO integration.inbox_receipts" in statement:
                before_inbox_insert.set()
                assert release_worker.wait(10.0), "Timed out releasing worker inbox insert"

        async def _revoke_inv_2() -> dict[str, Any]:
            engine = create_async_engine(_to_async_pg_url(app_url))
            factory = async_sessionmaker(engine, expire_on_commit=False)
            repo = InvitationRepository(PostgresOutboxEventBus(factory))

            @event.listens_for(engine.sync_engine, "before_cursor_execute")
            def _observe_ws(_conn, _cursor, statement, _params, _ctx, _many):
                if "FOR UPDATE OF w" in statement:
                    api_entered_ws.set()

            try:
                async with factory() as session, session.begin():
                    return await repo.revoke_invitation(
                        session,
                        tenant_id=seed["tenant_a"],
                        workspace_id=seed["ws_a"],
                        invitation_id=UUID(inv_2["invitation_id"]),
                        actor_user_id=seed["owner_a"],
                        request_id="req-comp-rev2",
                        trace_id="trace-comp-rev2",
                    )
            finally:
                await engine.dispose()

        with _open_consumer(app_url, seed, protector, sink.port, engine=worker_engine) as consumer:
            try:
                had, claim_1 = consumer._claim_next_outbox_delivery()
                assert (
                    had and claim_1 is not None and claim_1.invitation_id == inv_1["invitation_id"]
                )

                with ThreadPoolExecutor(max_workers=2) as executor:
                    comp_future = executor.submit(
                        consumer._complete_delivery_cas,
                        claim_1,
                        SmtpSendResult(outcome="provider_accepted"),
                    )
                    assert before_inbox_insert.wait(10.0)
                    rev_future = executor.submit(lambda: asyncio.run(_revoke_inv_2()))
                    assert api_entered_ws.wait(10.0)
                    release_worker.set()
                    comp_future.result(timeout=15.0)
                    rev2_res = rev_future.result(timeout=15.0)
            finally:
                release_worker.set()
                worker_engine.dispose()

        assert rev2_res["status"] == "revoked"
        with psycopg.connect(owner_url) as conn:
            assert conn.execute(
                "SELECT delivery_state, proof_ciphertext FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_1["invitation_id"]),),
            ).fetchone() == ("provider_accepted", None)
            assert conn.execute(
                "SELECT count(*) FROM integration.inbox_receipts WHERE event_id = %s",
                (UUID(inv_1["outbox_event_id"]),),
            ).fetchone() == (1,)

        # Part B: Stale CAS completion after resend (generation 1 vs committed generation 2)
        # MUST NOT overwrite generation 2 state/proof or write inbox_receipt for generation 1.
        inv_3 = asyncio.run(_repo_create_invitation(app_url, seed, protector, "comp-3@example.com"))
        with _open_consumer(app_url, seed, protector, sink.port) as consumer_3:
            had_3, claim_3 = consumer_3._claim_next_outbox_delivery()
            assert had_3 and claim_3 is not None and claim_3.invitation_id == inv_3["invitation_id"]

            async def _resend_inv_3() -> dict[str, Any]:
                engine = create_async_engine(_to_async_pg_url(app_url))
                factory = async_sessionmaker(engine, expire_on_commit=False)
                repo = InvitationRepository(PostgresOutboxEventBus(factory))
                try:
                    async with factory() as session, session.begin():
                        return await repo.resend_invitation(
                            session,
                            tenant_id=seed["tenant_a"],
                            workspace_id=seed["ws_a"],
                            invitation_id=UUID(inv_3["invitation_id"]),
                            actor_user_id=seed["owner_a"],
                            protector=protector,
                            policy=InvitationPolicyConfig(72, 0, 50, 100),
                            request_id="req-comp-res3",
                            trace_id="trace-comp-res3",
                        )
                finally:
                    await engine.dispose()

            resend_3 = asyncio.run(_resend_inv_3())
            assert resend_3["generation"] == 2

            # Stale worker attempts completion with gen=1 claim
            consumer_3._complete_delivery_cas(claim_3, SmtpSendResult(outcome="provider_accepted"))

        with psycopg.connect(owner_url) as conn:
            row_3 = conn.execute(
                "SELECT status, generation, delivery_state, proof_ciphertext IS NOT NULL "
                "FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_3["invitation_id"]),),
            ).fetchone()
            assert row_3 == ("pending", 2, "queued", True)
            assert conn.execute(
                "SELECT state FROM integration.outbox_events WHERE id = %s",
                (UUID(inv_3["outbox_event_id"]),),
            ).fetchone() == ("dead",)
            assert conn.execute(
                "SELECT count(*) FROM integration.inbox_receipts WHERE event_id = %s",
                (UUID(inv_3["outbox_event_id"]),),
            ).fetchone() == (0,)
    finally:
        sink.close()


@pytest.mark.integration
def test_two_consumers_competing_same_and_multiple_events_no_double_send(
    isolated_database: dict[str, Any],
):
    """Two scoped consumers racing under SKIP LOCKED never double-claim or double-send."""
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]
    run_migrations(owner_url, MIGRATIONS_DIR)
    seed = _seed_workspace_and_users(owner_url)
    protector = InvitationProofProtector(Fernet.generate_key().decode("ascii"), "v1")
    sink = _RealSmtpSinkServer("accept")

    try:
        inv_single = asyncio.run(
            _repo_create_invitation(app_url, seed, protector, "dual-single@example.com")
        )
        with (
            _open_consumer(app_url, seed, protector, sink.port) as c1,
            _open_consumer(app_url, seed, protector, sink.port) as c2,
        ):
            barrier = Barrier(2)

            def _race_claim_and_send(consumer: InvitationOutboxConsumer) -> bool:
                barrier.wait(timeout=10.0)
                return consumer.process_next_event()

            with ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(_race_claim_and_send, c1)
                f2 = executor.submit(_race_claim_and_send, c2)
                r1 = f1.result(timeout=15.0)
                r2 = f2.result(timeout=15.0)

            assert (r1 or r2) is True
            assert not (r1 and r2)
            assert len(sink.messages) == 1

            with psycopg.connect(owner_url) as conn:
                assert conn.execute(
                    "SELECT state, attempt FROM integration.outbox_events WHERE id = %s",
                    (UUID(inv_single["outbox_event_id"]),),
                ).fetchone() == ("delivered", 1)
                assert conn.execute(
                    "SELECT count(*) FROM integration.inbox_receipts WHERE event_id = %s",
                    (UUID(inv_single["outbox_event_id"]),),
                ).fetchone() == (1,)

            # 2. Batch of 3 events drained concurrently by 2 consumers -> exact 3 total emails
            for idx in range(3):
                asyncio.run(
                    _repo_create_invitation(
                        app_url, seed, protector, f"dual-batch-{idx}@example.com"
                    )
                )
            barrier2 = Barrier(2)

            def _drain(consumer: InvitationOutboxConsumer) -> int:
                barrier2.wait(timeout=10.0)
                return consumer.run_once()

            with ThreadPoolExecutor(max_workers=2) as executor:
                fd1 = executor.submit(_drain, c1)
                fd2 = executor.submit(_drain, c2)
                d1 = fd1.result(timeout=15.0)
                d2 = fd2.result(timeout=15.0)

            assert d1 + d2 >= 3
            assert len(sink.messages) == 4  # 1 from single + 3 from batch
    finally:
        sink.close()


@pytest.mark.integration
def test_pre_dispatch_recoverable_conflict_retries_cleanly_without_double_claim_and_continues(
    isolated_database: dict[str, Any],
):
    """Rolled-back pre-dispatch 40P01 recovers inside bounded retries, avoids double claim, and continues."""
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]
    run_migrations(owner_url, MIGRATIONS_DIR)
    seed = _seed_workspace_and_users(owner_url)
    protector = InvitationProofProtector(Fernet.generate_key().decode("ascii"), "v1")
    sink = _RealSmtpSinkServer("accept")

    class _FakeDeadlock(Exception):
        sqlstate = "40P01"

    try:
        inv1 = asyncio.run(_repo_create_invitation(app_url, seed, protector, "retry-1@example.com"))
        inv2 = asyncio.run(_repo_create_invitation(app_url, seed, protector, "retry-2@example.com"))

        engine = create_engine(_to_sync_pg_url(app_url), pool_pre_ping=True)
        injected_attempts = 0

        @event.listens_for(engine, "after_cursor_execute")
        def _inject_pre_dispatch_deadlock(_conn, _cursor, statement, _params, _ctx, _many):
            nonlocal injected_attempts
            if statement.startswith("UPDATE integration.outbox_events") and injected_attempts < 2:
                injected_attempts += 1
                raise OperationalError(
                    statement,
                    {},
                    _FakeDeadlock("simulated 40P01 deadlock before commit"),
                )

        with _open_consumer(
            app_url,
            seed,
            protector,
            sink.port,
            engine=engine,
            db_retry_max_attempts=3,
        ) as consumer:
            try:
                processed = consumer.run_once()
            finally:
                engine.dispose()

        assert injected_attempts == 2
        assert processed == 2
        assert len(sink.messages) == 2

        with psycopg.connect(owner_url) as conn:
            for iid, ob_id in (
                (UUID(inv1["invitation_id"]), UUID(inv1["outbox_event_id"])),
                (UUID(inv2["invitation_id"]), UUID(inv2["outbox_event_id"])),
            ):
                assert conn.execute(
                    "SELECT delivery_state FROM iam.workspace_invitations WHERE id = %s",
                    (iid,),
                ).fetchone() == ("provider_accepted",)
                assert conn.execute(
                    "SELECT state, attempt FROM integration.outbox_events WHERE id = %s",
                    (ob_id,),
                ).fetchone() == ("delivered", 1)

        inv3 = asyncio.run(_repo_create_invitation(app_url, seed, protector, "retry-3@example.com"))
        engine_loop = create_engine(_to_sync_pg_url(app_url), pool_pre_ping=True)
        loop_failures = 0

        @event.listens_for(engine_loop, "before_cursor_execute")
        def _fail_first_poll(_conn, _cursor, statement, _params, _ctx, _many):
            nonlocal loop_failures
            if "FROM integration.outbox_events" in statement and loop_failures < 2:
                loop_failures += 1
                raise OperationalError(statement, {}, _FakeDeadlock("simulated burst 40P01"))

        with _open_consumer(
            app_url,
            seed,
            protector,
            sink.port,
            engine=engine_loop,
            db_retry_max_attempts=2,
        ) as consumer_loop:

            async def _run_and_stop() -> None:
                task = asyncio.create_task(consumer_loop.run_forever(poll_interval_seconds=0.05))
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    with psycopg.connect(owner_url) as check_conn:
                        state = check_conn.execute(
                            "SELECT delivery_state FROM iam.workspace_invitations WHERE id = %s",
                            (UUID(inv3["invitation_id"]),),
                        ).fetchone()
                    if state == ("provider_accepted",):
                        break
                    await asyncio.sleep(0.05)
                consumer_loop.stop()
                await asyncio.wait_for(task, timeout=5.0)

            try:
                asyncio.run(_run_and_stop())
            finally:
                engine_loop.dispose()

        assert loop_failures == 2
        assert len(sink.messages) == 3
    finally:
        sink.close()


@pytest.mark.integration
def test_provider_accepted_completion_db_failure_no_smtp_retry_and_lease_reclaims_unknown(
    isolated_database: dict[str, Any],
):
    """If SMTP already returned 250 OK and DB completion fails, never blind-retry SMTP; lease expires to unknown."""
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]
    run_migrations(owner_url, MIGRATIONS_DIR)
    seed = _seed_workspace_and_users(owner_url)
    protector = InvitationProofProtector(Fernet.generate_key().decode("ascii"), "v1")
    sink = _RealSmtpSinkServer("accept")

    class _ConnDropped(Exception):
        sqlstate = "08006"

    try:
        inv = asyncio.run(
            _repo_create_invitation(app_url, seed, protector, "post-smtp-err@example.com")
        )
        inv_id = UUID(inv["invitation_id"])
        outbox_id = UUID(inv["outbox_event_id"])

        engine = create_engine(_to_sync_pg_url(app_url), pool_pre_ping=True)
        completion_attempts = 0

        @event.listens_for(engine, "before_cursor_execute")
        def _fail_completion_cas(_conn, _cursor, statement, _params, _ctx, _many):
            nonlocal completion_attempts
            if "SET delivery_state = 'provider_accepted'" in statement:
                completion_attempts += 1
                raise OperationalError(
                    statement, {}, _ConnDropped("simulated DB outage at completion")
                )

        with _open_consumer(
            app_url,
            seed,
            protector,
            sink.port,
            engine=engine,
            db_retry_max_attempts=2,
        ) as consumer:
            try:
                with pytest.raises(OperationalError) as exc_info:
                    consumer.process_next_event()
                assert _extract_sqlstate(exc_info.value) == "08006"
            finally:
                engine.dispose()

        assert completion_attempts == 2
        assert len(sink.messages) == 1

        with _open_consumer(app_url, seed, protector, sink.port) as clean_consumer:
            assert clean_consumer.process_next_event() is False
            assert len(sink.messages) == 1

            with psycopg.connect(owner_url) as conn:
                assert conn.execute(
                    "SELECT delivery_state, lease_token IS NOT NULL FROM iam.workspace_invitations WHERE id = %s",
                    (inv_id,),
                ).fetchone() == ("sending", True)
                assert conn.execute(
                    "SELECT state FROM integration.outbox_events WHERE id = %s",
                    (outbox_id,),
                ).fetchone() == ("delivering",)
                assert conn.execute(
                    "SELECT count(*) FROM integration.inbox_receipts WHERE event_id = %s",
                    (outbox_id,),
                ).fetchone() == (0,)

                conn.execute(
                    "UPDATE iam.workspace_invitations SET leased_until = now() - interval '5 seconds' WHERE id = %s",
                    (inv_id,),
                )

            reclaimed = clean_consumer.reclaim_expired_leases()
            assert reclaimed == 1
            assert len(sink.messages) == 1

        with psycopg.connect(owner_url) as conn:
            assert conn.execute(
                "SELECT delivery_state, delivery_error_code, proof_ciphertext, lease_token "
                "FROM iam.workspace_invitations WHERE id = %s",
                (inv_id,),
            ).fetchone() == ("unknown", "LEASE_EXPIRED_UNKNOWN", None, None)
            assert conn.execute(
                "SELECT state FROM integration.outbox_events WHERE id = %s",
                (outbox_id,),
            ).fetchone() == ("dead",)
            assert conn.execute(
                "SELECT count(*) FROM integration.inbox_receipts WHERE event_id = %s",
                (outbox_id,),
            ).fetchone() == (0,)
    finally:
        sink.close()
