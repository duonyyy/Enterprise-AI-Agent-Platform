"""Real Redis regressions for exact-instance post-commit handoff cleanup (R04)."""

from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest
from control_api.modules.workspaces.repositories.handoff_store import (
    InvitationHandoffProtector,
    RedisInvitationHandoffStore,
    generate_browser_binding_token,
    hash_browser_binding_token,
)
from cryptography.fernet import Fernet
from platform_common.invitations import hash_invitation_proof


@pytest.mark.integration
@pytest.mark.parametrize("replacement", ["expired", "same_deadline"])
def test_redis_stale_cleanup_preserves_new_same_proof_handoff(replacement: str) -> None:
    # This test deletes only its own UUID key; require an explicit test Redis URL.
    redis_url = os.environ.get("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("TEST_REDIS_URL must point to an isolated test Redis")
    reader = RedisInvitationHandoffStore(redis_url=redis_url)
    cleaner = RedisInvitationHandoffStore(redis_url=redis_url)
    protector = InvitationHandoffProtector(fernet_key=Fernet.generate_key().decode())
    invitation_id = str(uuid4())
    binding = hash_browser_binding_token(generate_browser_binding_token())
    proof = "same-proof-across-contexts"
    proof_hash = hash_invitation_proof(proof)
    user_id = str(uuid4())
    key = f"invite:handoff:{invitation_id}:{binding}"

    def create(raw_proof: str, now: int, ttl: int) -> str:
        ciphertext = protector.encrypt_handoff(
            invitation_id=invitation_id,
            raw_proof=raw_proof,
            browser_binding_digest=binding,
            issued_at=now,
            expires_at=now + ttl,
        )
        reader.save_or_get_encrypted_handoff(
            invitation_id=invitation_id,
            browser_binding_digest=binding,
            token_hash=hash_invitation_proof(raw_proof),
            key_id=protector.key_id,
            ciphertext=ciphertext,
            expires_at=now + ttl,
            ttl_seconds=ttl,
            now=now,
        )
        return ciphertext

    try:
        t0 = int(time.time())
        old_ttl = 1 if replacement == "expired" else 600
        old_ciphertext = create(proof, t0, old_ttl)
        if replacement == "expired":
            # Accelerate only this adapter fixture's old TTL, not production settings.
            wait_deadline = time.monotonic() + 3
            while reader._client.exists(key) and time.monotonic() < wait_deadline:
                time.sleep(0.03)
            assert not reader._client.exists(key)
            fresh_time = int(time.time())
        else:
            # A -> B -> A can reuse hash AND expiry; the encrypted instance still differs.
            create("intermediate-different-proof", t0, 600)
            fresh_time = t0
        new_ciphertext = create(proof, fresh_time, 600)
        assert new_ciphertext != old_ciphertext
        before = reader._client.get(key)
        ttl_before = reader._client.pttl(key)
        assert before is not None and ttl_before > 590_000

        cleaner.convert_to_consumed_receipt(
            invitation_id=invitation_id,
            browser_binding_digest=binding,
            user_id=user_id,
            token_hash=proof_hash,
            expected_handoff_ciphertext=old_ciphertext,
            expires_at=t0 + old_ttl,
        )
        assert reader._client.get(key) == before
        assert 0 < reader._client.pttl(key) <= ttl_before

        ttl_before_consume = reader._client.pttl(key)
        cleaner.convert_to_consumed_receipt(
            invitation_id=invitation_id,
            browser_binding_digest=binding,
            user_id=user_id,
            token_hash=proof_hash,
            expected_handoff_ciphertext=new_ciphertext,
            expires_at=fresh_time + 600,
        )
        receipt = reader.get_handoff_entry(
            invitation_id=invitation_id, browser_binding_digest=binding
        )
        assert receipt is not None and receipt["kind"] == "consumed_receipt"
        assert receipt["expires_at"] == fresh_time + 600
        assert 0 < reader._client.pttl(key) <= ttl_before_consume

        saved_receipt = reader._client.get(key)
        cleaner.convert_to_consumed_receipt(
            invitation_id=invitation_id,
            browser_binding_digest=binding,
            user_id=user_id,
            token_hash=proof_hash,
            expected_handoff_ciphertext=old_ciphertext,
            expires_at=t0 + old_ttl,
        )
        assert reader._client.get(key) == saved_receipt
    finally:
        reader._client.delete(key)
        reader._client.close()
        cleaner._client.close()
