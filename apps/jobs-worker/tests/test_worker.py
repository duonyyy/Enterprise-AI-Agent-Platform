"""Worker boot guards.

Queue leasing and RLS are PostgreSQL integration concerns; no dict-backed queue
is used here as a replacement for runtime behaviour.
"""

import pytest

from jobs_worker.worker import JobWorker


def test_worker_rejects_non_postgresql_database_url():
    with pytest.raises(ValueError, match="PostgreSQL"):
        JobWorker(
            database_url="sqlite:///:memory:",
            tenant_id="00000000-0000-0000-0000-000000000001",
            workspace_id="00000000-0000-0000-0000-000000000011",
        )


def test_worker_requires_valid_scope_ids():
    with pytest.raises(ValueError):
        JobWorker(
            database_url="postgresql+psycopg://platform_app:secret@db/platform",
            tenant_id="not-a-uuid",
            workspace_id="00000000-0000-0000-0000-000000000011",
        )
