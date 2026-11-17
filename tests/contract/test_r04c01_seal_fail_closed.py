"""
R04C-01  Deferred seal check must FAIL CLOSED

VERIFIED_SOURCE test — static text analysis of the migration SQL.
No runtime PostgreSQL required.
"""

import pathlib
import re
import textwrap

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "database"
    / "migrations"
    / "0007_agent_publish_receipts_and_binding_seals.sql"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_function_body(sql: str, fn_name: str) -> str:
    """Return the PL/pgSQL body (between $$ … $$) for *fn_name*."""
    # Pattern: CREATE OR REPLACE FUNCTION <fn_name>() … AS $$ <body> $$;
    pattern = (
        rf"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+{re.escape(fn_name)}\s*\("
        r"[^$]*\$\$\s*(.*?)\s*\$\$"
    )
    m = re.search(pattern, sql, re.DOTALL | re.IGNORECASE)
    assert m, f"Function {fn_name} not found in migration SQL"
    return m.group(1)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSealCheckFailClosed:
    """Verify the NOT FOUND branch raises instead of returning."""

    FN = "catalog.check_agent_version_sealed"

    @staticmethod
    def _body() -> str:
        sql = MIGRATION.read_text(encoding="utf-8")
        return _extract_function_body(sql, "catalog.check_agent_version_sealed")

    # -- positive: fail-closed RAISE must be present --------------------------

    def test_not_found_raises_exception(self):
        body = self._body()
        # After "IF NOT FOUND", the very next non-blank statement must be a
        # RAISE EXCEPTION mentioning visibility / verification.
        assert re.search(
            r"IF\s+NOT\s+FOUND\s+THEN\s+RAISE\s+EXCEPTION",
            body,
            re.IGNORECASE | re.DOTALL,
        ), (
            "NOT FOUND branch must RAISE EXCEPTION (fail closed). "
            "Got:\n" + textwrap.indent(body, "  ")
        )

    def test_raise_message_mentions_not_visible(self):
        body = self._body()
        assert re.search(
            r"RAISE\s+EXCEPTION\s+'[^']*not visible[^']*'",
            body,
            re.IGNORECASE,
        ), "RAISE EXCEPTION message should mention 'not visible'"

    # -- negative: old fail-open pattern must be absent -----------------------

    def test_no_return_new_in_not_found_branch(self):
        body = self._body()
        # The old pattern: "IF NOT FOUND THEN … RETURN NEW" (before the next END IF)
        fail_open = re.search(
            r"IF\s+NOT\s+FOUND\s+THEN\b[^;]*?\bRETURN\s+NEW\b",
            body,
            re.IGNORECASE | re.DOTALL,
        )
        assert fail_open is None, (
            "NOT FOUND branch still contains RETURN NEW (fails open)"
        )

    def test_no_row_was_deleted_comment(self):
        body = self._body()
        assert "Row was deleted" not in body, (
            "Stale comment 'Row was deleted' should be removed from the NOT FOUND branch"
        )
