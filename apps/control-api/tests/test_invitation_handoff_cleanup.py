"""Regression coverage for cleanup bound to an exact handoff instance (R04)."""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest
from control_api.core.settings import settings
from control_api.modules.identity.models.session import SessionData, VerifiedIdentityEvidence
from control_api.modules.workspaces.repositories.invitation_repository import InvitationRepository
from control_api.modules.workspaces.services.handoff_store import (
    InMemoryInvitationHandoffStore,
    InvitationHandoffProtector,
    generate_browser_binding_token,
    hash_browser_binding_token,
)
from control_api.modules.workspaces.services.invitation_acceptance_service import (
    InvitationAcceptanceService,
)
from cryptography.fernet import Fernet
from platform_common.invitations import hash_invitation_proof
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.parametrize("replacement", ["expired", "same_deadline"])
def test_stale_cleanup_preserves_new_same_proof_handoff(replacement: str) -> None:
    store = InMemoryInvitationHandoffStore()
    protector = InvitationHandoffProtector(fernet_key=Fernet.generate_key().decode())
    invitation_id = str(uuid4())
    binding = hash_browser_binding_token(generate_browser_binding_token())
    proof = "same-proof-across-contexts"
    proof_hash = hash_invitation_proof(proof)
    user_id = str(uuid4())
    t0 = 1_800_000_000

    def create(raw_proof: str, now: int) -> str:
        ciphertext = protector.encrypt_handoff(
            invitation_id=invitation_id,
            raw_proof=raw_proof,
            browser_binding_digest=binding,
            issued_at=now,
            expires_at=now + 600,
        )
        store.save_or_get_encrypted_handoff(
            invitation_id=invitation_id,
            browser_binding_digest=binding,
            token_hash=hash_invitation_proof(raw_proof),
            key_id=protector.key_id,
            ciphertext=ciphertext,
            expires_at=now + 600,
            ttl_seconds=600,
            now=now,
        )
        return ciphertext

    old_ciphertext = create(proof, t0)
    if replacement == "expired":
        fresh_time = t0 + 601
    else:
        # An A -> B -> A replacement can share both proof hash and expiry second.
        create("intermediate-different-proof", t0)
        fresh_time = t0
    new_ciphertext = create(proof, fresh_time)
    assert new_ciphertext != old_ciphertext
    before = store.get_handoff_entry(
        invitation_id=invitation_id, browser_binding_digest=binding, now=fresh_time
    )
    assert before is not None and before["ciphertext"] == new_ciphertext

    store.convert_to_consumed_receipt(
        invitation_id=invitation_id,
        browser_binding_digest=binding,
        user_id=user_id,
        token_hash=proof_hash,
        expected_handoff_ciphertext=old_ciphertext,
        expires_at=t0 + 600,
        now=fresh_time,
    )
    assert (
        store.get_handoff_entry(
            invitation_id=invitation_id, browser_binding_digest=binding, now=fresh_time
        )
        == before
    )

    store.convert_to_consumed_receipt(
        invitation_id=invitation_id,
        browser_binding_digest=binding,
        user_id=user_id,
        token_hash=proof_hash,
        expected_handoff_ciphertext=new_ciphertext,
        expires_at=fresh_time + 600,
        now=fresh_time + 1,
    )
    receipt = store.get_handoff_entry(
        invitation_id=invitation_id, browser_binding_digest=binding, now=fresh_time + 1
    )
    assert receipt is not None and receipt["kind"] == "consumed_receipt"
    assert receipt["expires_at"] == before["expires_at"]


def test_accept_cleanup_uses_handoff_read_before_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing the context during the DB await cannot redirect post-commit cleanup."""
    monkeypatch.setattr(settings, "invitations_enabled", True)
    handoff_key = Fernet.generate_key().decode()
    proof_key = Fernet.generate_key().decode()
    monkeypatch.setattr(
        settings.invitation_handoff_encryption_key, "get_secret_value", lambda: handoff_key
    )
    monkeypatch.setattr(
        settings.invitation_proof_encryption_key, "get_secret_value", lambda: proof_key
    )
    store = InMemoryInvitationHandoffStore()
    repository = InvitationRepository()
    service = InvitationAcceptanceService(repository=repository, store=store)
    now = int(time.time())
    invitation_id, user_id, workspace_id = uuid4(), str(uuid4()), uuid4()
    proof = "proof-held-across-database-await"
    _, cookie = service.create_handoff(
        invitation_id=invitation_id,
        raw_token=proof,
        existing_browser_cookie=None,
        client_ip="127.0.0.101",
        now=now,
    )
    binding = hash_browser_binding_token(cookie)
    before = store.get_handoff_entry(
        invitation_id=str(invitation_id), browser_binding_digest=binding, now=now
    )
    assert before is not None
    replacement: dict[str, object] = {}

    async def commit_and_replace(_session: AsyncSession, **_kwargs: object) -> dict[str, object]:
        for token in ("intermediate-proof", proof):
            service.create_handoff(
                invitation_id=invitation_id,
                raw_token=token,
                existing_browser_cookie=cookie,
                client_ip="127.0.0.101",
                now=now,
            )
        current = store.get_handoff_entry(
            invitation_id=str(invitation_id), browser_binding_digest=binding, now=now
        )
        assert current is not None and current["ciphertext"] != before["ciphertext"]
        replacement.update(current)
        return {
            "invitation_id": str(invitation_id),
            "tenant_id": str(uuid4()),
            "workspace_id": str(workspace_id),
            "workspace_slug": "test-workspace",
            "workspace_name": "Test Workspace",
            "role": "developer",
            "acceptance_state": "accepted",
        }

    monkeypatch.setattr(repository, "accept_invitation", commit_and_replace)
    session_data = SessionData(
        session_id=str(uuid4()),
        user_id=user_id,
        issuer=settings.oidc_issuer,
        subject="test-subject",
        created_at=now,
        idle_expires_at=now + 1800,
        absolute_expires_at=now + 28800,
        identity_evidence=VerifiedIdentityEvidence(
            issuer=settings.oidc_issuer,
            subject="test-subject",
            user_id=user_id,
            email_normalized="test@example.com",
            email_verified=True,
            id_token_issued_at=now,
            observed_at=now,
        ),
    )

    async def accept() -> None:
        async with AsyncSession() as session:
            result = await service.accept_invitation(
                session,
                invitation_id=invitation_id,
                session_data=session_data,
                issuer=settings.oidc_issuer,
                subject="test-subject",
                user_id=user_id,
                browser_cookie=cookie,
                request_id="test-request",
                trace_id="test-trace",
                now=now,
            )
            assert result.acceptance_state == "accepted"

    asyncio.run(accept())
    assert (
        store.get_handoff_entry(
            invitation_id=str(invitation_id), browser_binding_digest=binding, now=now
        )
        == replacement
    )
