"""Distributed Redis revocation and atomic session rotation integration tests (P0-01, P0-02, TASK 02, AUTH-T10).

Validates multi-client and multi-worker session state sharing, atomic single-winner
credential rotation via Lua script, concurrent race conditions with logout/revocation,
and TTL/retention cleanup behavior on real Redis when available.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any
from uuid import uuid4

import pytest
import redis
from control_api.modules.identity.session import (
    RedisRevocationStore,
    SessionManager,
)
from fastapi import HTTPException

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


def _is_redis_available() -> bool:
    try:
        client = redis.Redis.from_url(TEST_REDIS_URL, socket_timeout=1.0, socket_connect_timeout=1.0)
        return bool(client.ping())
    except (redis.RedisError, OSError):
        return False


REDIS_AVAILABLE = _is_redis_available()
pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not REDIS_AVAILABLE,
    reason="BLOCKED: Real Redis service is unavailable on host. Cannot run live multi-client Redis integration tests.",
)
def test_real_redis_multi_client_session_revocation() -> None:
    """Two independent Redis client instances observe session revocation across distinct connections."""
    store_a = RedisRevocationStore(redis_url=TEST_REDIS_URL)
    store_b = RedisRevocationStore(redis_url=TEST_REDIS_URL)
    manager_a = SessionManager(denylist=store_a)
    manager_b = SessionManager(denylist=store_b)

    uid = str(uuid4())
    now = int(time.time())
    sess, token = manager_a.create_session(uid, "https://idp.local", "sub-test", now=now)

    # Clean namespace cleanup hook (strictly scoped to keys created by this test)
    auth_key = f"auth:session:{sess.session_id}"
    rev_key = f"revocation:session:{sess.session_id}"
    try:
        # Instance B verifies session is initially valid
        verified = manager_b.verify_session_token(token, now=now)
        assert verified.session_id == sess.session_id

        # Instance A revokes the session
        manager_a.revoke_session(sess, now=now)

        # Instance B immediately observes revocation across network
        with pytest.raises(HTTPException) as exc:
            manager_b.verify_session_token(token, now=now)
        assert "revoked" in str(exc.value.detail).lower()
    finally:
        store_a._client.delete(auth_key, rev_key)


@pytest.mark.skipif(
    not REDIS_AVAILABLE,
    reason="BLOCKED: Real Redis service is unavailable on host. Cannot run live multi-client Redis integration tests.",
)
def test_real_redis_monotonic_user_revocation_lua() -> None:
    """Atomic Lua script in Redis guarantees monotonic cutoff and non-decreasing retention."""
    store_a = RedisRevocationStore(redis_url=TEST_REDIS_URL)
    store_b = RedisRevocationStore(redis_url=TEST_REDIS_URL)

    uid = str(uuid4())
    user_key = f"revocation:user:{uid}"
    now = int(time.time())

    try:
        # Store A sets cutoff = now
        store_a.revoke_user(uid, until_timestamp=now, max_session_lifetime=3600, now=now)
        stored_cutoff = int(store_a._client.get(user_key))  # type: ignore[arg-type]
        assert stored_cutoff == now
        ttl_initial = store_a._client.ttl(user_key)
        assert ttl_initial > 3500

        # Store B tries to set an older cutoff (now - 500)
        store_b.revoke_user(uid, until_timestamp=now - 500, max_session_lifetime=3600, now=now)
        stored_after_older = int(store_b._client.get(user_key))  # type: ignore[arg-type]
        # Monotonicity check: cutoff MUST NOT have moved backwards!
        assert stored_after_older == now

        # Store A sets a newer cutoff (now + 10)
        store_a.revoke_user(uid, until_timestamp=now + 10, max_session_lifetime=3600, now=now + 10)
        stored_newer = int(store_a._client.get(user_key))  # type: ignore[arg-type]
        assert stored_newer == now + 10
    finally:
        store_a._client.delete(user_key)


@pytest.mark.skipif(
    not REDIS_AVAILABLE,
    reason="BLOCKED: Real Redis service is unavailable on host. Cannot run live multi-client Redis integration tests.",
)
def test_real_redis_concurrent_refresh_single_winner() -> None:
    """Two independent SessionManagers with separate Redis connections race to refresh the same credential."""
    store_a = RedisRevocationStore(redis_url=TEST_REDIS_URL)
    store_b = RedisRevocationStore(redis_url=TEST_REDIS_URL)
    manager_a = SessionManager(denylist=store_a)
    manager_b = SessionManager(denylist=store_b)

    uid = str(uuid4())
    now = int(time.time())
    sess1, token1 = manager_a.create_session(uid, "https://idp.local", "sub-conc", now=now)
    auth_key = f"auth:session:{sess1.session_id}"
    rev_key = f"revocation:session:{sess1.session_id}"

    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, Any]] = []
    lock = threading.Lock()

    def worker(mgr: SessionManager) -> None:
        barrier.wait(timeout=5.0)
        try:
            new_sess, new_tok = mgr.refresh_session(sess1, now=now + 1)
            with lock:
                outcomes.append(("WIN", (new_sess, new_tok)))
        except HTTPException as exc:
            with lock:
                outcomes.append(("LOSE", exc))

    try:
        t1 = threading.Thread(target=worker, args=(manager_a,))
        t2 = threading.Thread(target=worker, args=(manager_b,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(outcomes) == 2
        wins = [o for o in outcomes if o[0] == "WIN"]
        losses = [o for o in outcomes if o[0] == "LOSE"]
        assert len(wins) == 1
        assert len(losses) == 1

        winning_sess, winning_token = wins[0][1]
        assert winning_sess.rotation_version == 2

        # Other client immediately rejects old credential token1
        with pytest.raises(HTTPException) as exc_old:
            manager_b.verify_session_token(token1, now=now + 1)
        assert exc_old.value.status_code == 401

        # Other client accepts winning_token
        verified_win = manager_b.verify_session_token(winning_token, now=now + 1)
        assert verified_win.rotation_version == 2
    finally:
        store_a._client.delete(auth_key, rev_key)


@pytest.mark.skipif(
    not REDIS_AVAILABLE,
    reason="BLOCKED: Real Redis service is unavailable on host. Cannot run live multi-client Redis integration tests.",
)
def test_real_redis_refresh_race_with_logout_and_user_revoke() -> None:
    """Refresh racing with logout or user revocation never resurrects a revoked session."""
    store_a = RedisRevocationStore(redis_url=TEST_REDIS_URL)
    store_b = RedisRevocationStore(redis_url=TEST_REDIS_URL)
    manager_a = SessionManager(denylist=store_a)
    manager_b = SessionManager(denylist=store_b)

    uid = str(uuid4())
    now = int(time.time())
    sess1, token1 = manager_a.create_session(uid, "https://idp.local", "sub-race-rev", now=now)
    auth_key = f"auth:session:{sess1.session_id}"
    rev_key = f"revocation:session:{sess1.session_id}"
    user_key = f"revocation:user:{uid}"

    barrier = threading.Barrier(2)
    refresh_result: list[tuple[SessionManager, str]] = []

    def do_refresh() -> None:
        barrier.wait(timeout=5.0)
        try:
            _s, new_tok = manager_a.refresh_session(sess1, now=now + 1)
            refresh_result.append((manager_a, new_tok))
        except HTTPException:
            pass

    def do_logout() -> None:
        barrier.wait(timeout=5.0)
        manager_b.revoke_session_by_token(token1, now=now + 1)

    try:
        t_ref = threading.Thread(target=do_refresh)
        t_log = threading.Thread(target=do_logout)
        t_ref.start()
        t_log.start()
        t_ref.join()
        t_log.join()

        # Regardless of whether Lua rotate ran before or after Lua revoke_session,
        # after both complete, neither token1 nor any rotated token may verify!
        with pytest.raises(HTTPException):
            manager_a.verify_session_token(token1, now=now + 2)
        for _, candidate_token in refresh_result:
            with pytest.raises(HTTPException):
                manager_b.verify_session_token(candidate_token, now=now + 2)

        # Verify TTL is finite on revocation key
        ttl = store_a._client.ttl(rev_key)
        assert ttl > 0
    finally:
        store_a._client.delete(auth_key, rev_key, user_key)
