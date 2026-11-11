"""In-memory testing adapter for authoritative session and revocation storage (SPEC-AUTH-001).

Re-exports InMemoryRevocationStore and RevocationDenylist from repositories.memory_session_store.
"""

from __future__ import annotations

from control_api.modules.identity.repositories.memory_session_store import (
    InMemoryRevocationStore,
    RevocationDenylist,
)

__all__ = ["InMemoryRevocationStore", "RevocationDenylist"]
