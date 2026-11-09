"""Distributed Redis revocation integration tests (P0-01, P0-02, AUTH-T10).

Validates multi-client and multi-worker revocation sharing, atomic Lua script
execution, and retention behavior on real Redis when available.
"""

from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest
import redis
from control_api.modules.identity.session import (
    RedisRevocationStore,
    SessionManager,
)

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

    # Clean namespace cleanup hook
    sess_key = f"revocation:session:{sess.session_id}"
    try:
        # Instance B verifies session is initially valid
        verified = manager_b.verify_session_token(token, now=now)
        assert verified.session_id == sess.session_id

        # Instance A revokes the session
        manager_a.revoke_session(sess)

        # Instance B immediately observes revocation across network
        with pytest.raises(Exception) as exc:
            manager_b.verify_session_token(token, now=now)
        assert "revoked" in str(exc.value.detail).lower()
    finally:
        store_a._client.delete(sess_key)


@pytest.mark.skipif(
    not REDIS_AVAILABLE,
    reason="BLOCKED: Real Redis service is unavailable on host. Cannot run live multi-client Redis integration tests.",
)
def test_real_redis_monotonic_user_revocation_lua() -> None:
    """Atomic Lua script in Redis guarantees monotonic cutoff and non-decreasing retention."""
    store_a = RedisRevocationStore(redis_url=TEST_REDIS_URL)
    store_b = RedisRevocationStore(redis_url=TEST_REDIS_URL)

    uid = f"test-user-{uuid4()}"
    user_key = f"revocation:user:{uid}"
    now = int(time.time())

    try:
        # Store A sets cutoff = now
        store_a.revoke_user(uid, until_timestamp=now, max_session_lifetime=3600)
        stored_cutoff = int(store_a._client.get(user_key))
        assert stored_cutoff == now
        ttl_initial = store_a._client.ttl(user_key)
        assert ttl_initial > 3500

        # Store B tries to set an older cutoff (now - 500)
        store_b.revoke_user(uid, until_timestamp=now - 500, max_session_lifetime=3600)
        stored_after_older = int(store_b._client.get(user_key))
        # Monotonicity check: cutoff MUST NOT have moved backwards!
        assert stored_after_older == now

        # Store A sets a newer cutoff (now + 10)
        store_a.revoke_user(uid, until_timestamp=now + 10, max_session_lifetime=3600)
        stored_newer = int(store_a._client.get(user_key))
        assert stored_newer == now + 10
    finally:
        store_a._client.delete(user_key)
