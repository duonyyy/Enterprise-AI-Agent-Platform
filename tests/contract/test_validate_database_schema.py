"""Contract and regression tests for scripts/validate_database_schema.py."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_validator_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "validate_database_schema.py"
    spec = importlib.util.spec_from_file_location("validate_database_schema", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _copy_migrations(tmp_path: Path) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    src_dir = repo_root / "database" / "migrations"
    dst_dir = tmp_path / "migrations"
    shutil.copytree(src_dir, dst_dir)
    return dst_dir


def test_canonical_migrations_pass_module_and_cli() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    validator = _load_validator_module()
    errors = validator.validate_database_schema(repo_root / "database" / "migrations")
    assert errors == []

    result = subprocess.run(
        [sys.executable, "-B", str(repo_root / "scripts" / "validate_database_schema.py")],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "PASS static checks:" in result.stdout
    assert "reviewed 0002 content fingerprint" in result.stdout
    assert "reviewed 0003 content fingerprint" in result.stdout
    assert "0003_restore_workspaces_rls.sql restores iam.workspaces tenant_isolation policy" in result.stdout
    assert "fail-closed RLS policy." not in result.stdout


def test_missing_migration_file_fails(tmp_path: Path) -> None:
    validator = _load_validator_module()
    migrations_dir = _copy_migrations(tmp_path)
    (migrations_dir / "0002_fail_closed_rls.sql").unlink()

    errors = validator.validate_database_schema(migrations_dir)
    assert any("Missing required canonical migration file: 0002_fail_closed_rls.sql" in e for e in errors)
    assert validator.main(["--migrations-dir", str(migrations_dir)]) == 1


def test_non_sequential_or_duplicate_migration_filename_fails(tmp_path: Path) -> None:
    validator = _load_validator_module()
    migrations_dir = _copy_migrations(tmp_path)
    (migrations_dir / "0005_unapproved.sql").write_text("BEGIN;\nCOMMIT;\n", encoding="utf-8")

    errors = validator.validate_database_schema(migrations_dir)
    assert any("Non-sequential migration numbering" in e for e in errors)
    assert any("Unexpected unapproved migration files" in e for e in errors)


def test_missing_domain_table_fails(tmp_path: Path) -> None:
    validator = _load_validator_module()
    migrations_dir = _copy_migrations(tmp_path)
    baseline_path = migrations_dir / "0001_baseline.sql"
    sql = baseline_path.read_text(encoding="utf-8")

    # Rename ops.eval_results to a non-canonical table name.
    corrupted = sql.replace("ops.eval_results", "ops.non_canonical_results")
    baseline_path.write_text(corrupted, encoding="utf-8")

    errors = validator.validate_database_schema(migrations_dir)
    assert any("Missing canonical tables" in e and "ops.eval_results" in e for e in errors)
    assert any("Unexpected non-canonical tables" in e and "ops.non_canonical_results" in e for e in errors)


def test_forward_foreign_key_reference_fails(tmp_path: Path) -> None:
    validator = _load_validator_module()
    migrations_dir = _copy_migrations(tmp_path)
    baseline_path = migrations_dir / "0001_baseline.sql"
    sql = baseline_path.read_text(encoding="utf-8")

    # Inject a forward reference from iam.tenants to ops.eval_runs (defined much later).
    target = "CREATE TABLE iam.tenants (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
    replacement = (
        "CREATE TABLE iam.tenants (\n"
        "    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n"
        "    latest_eval_run_id uuid REFERENCES ops.eval_runs(id),"
    )
    assert target in sql
    baseline_path.write_text(sql.replace(target, replacement, 1), encoding="utf-8")

    errors = validator.validate_database_schema(migrations_dir)
    assert any("iam.tenants: forward reference to ops.eval_runs" in e for e in errors)


def test_missing_composite_unique_target_or_scope_column_fails(tmp_path: Path) -> None:
    validator = _load_validator_module()
    migrations_dir = _copy_migrations(tmp_path)
    baseline_path = migrations_dir / "0001_baseline.sql"
    sql = baseline_path.read_text(encoding="utf-8")

    # Remove UNIQUE (tenant_id, id) from iam.workspaces, breaking composite FKs.
    target = "    UNIQUE (tenant_id, id),\n    UNIQUE (tenant_id, slug)"
    replacement = "    UNIQUE (tenant_id, slug)"
    assert target in sql
    baseline_path.write_text(sql.replace(target, replacement, 1), encoding="utf-8")

    errors = validator.validate_database_schema(migrations_dir)
    assert any("iam.workspaces: missing required UNIQUE (tenant_id, id)" in e for e in errors)
    assert any("target iam.workspaces missing UNIQUE/PK ['tenant_id', 'id']" in e for e in errors)


@pytest.mark.parametrize(
    ("needle", "expected_error_substring"),
    [
        (
            "ALTER TABLE ops.eval_results FORCE ROW LEVEL SECURITY;",
            "FORCE ROW LEVEL SECURITY mismatch",
        ),
        (
            (
                "CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON governance.audit_events "
                "FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();"
            ),
            "Immutable trigger mismatch",
        ),
        (
            (
                "CREATE TRIGGER guard_document_version_before_update BEFORE UPDATE ON knowledge.document_versions "
                "FOR EACH ROW EXECUTE FUNCTION governance.guard_document_version();"
            ),
            "Missing or duplicate guard_document_version_before_update trigger",
        ),
    ],
)
def test_missing_rls_or_immutability_trigger_fails(
    tmp_path: Path, needle: str, expected_error_substring: str
) -> None:
    validator = _load_validator_module()
    migrations_dir = _copy_migrations(tmp_path)
    baseline_path = migrations_dir / "0001_baseline.sql"
    sql = baseline_path.read_text(encoding="utf-8")
    assert needle in sql
    baseline_path.write_text(sql.replace(needle, "", 1), encoding="utf-8")

    errors = validator.validate_database_schema(migrations_dir)
    assert any(expected_error_substring in e for e in errors)


def test_corrupted_fail_closed_rls_migration_fails(tmp_path: Path) -> None:
    validator = _load_validator_module()
    migrations_dir = _copy_migrations(tmp_path)
    rls_path = migrations_dir / "0002_fail_closed_rls.sql"
    sql = rls_path.read_text(encoding="utf-8")

    corrupted = sql.replace(
        "COMMIT;",
        "-- reintroduce permissive IS NULL fallback\nCREATE TABLE ops.extra_bad (id uuid);\nCOMMIT;",
    )
    rls_path.write_text(corrupted, encoding="utf-8")

    errors = validator.validate_database_schema(migrations_dir)
    assert any("0002_fail_closed_rls.sql must not contain CREATE TABLE" in e for e in errors)
    assert any("differs from the reviewed authored SQL fingerprint" in e for e in errors)


def test_corrupted_restore_workspaces_rls_migration_fails(tmp_path: Path) -> None:
    validator = _load_validator_module()
    migrations_dir = _copy_migrations(tmp_path)
    restore_path = migrations_dir / "0003_restore_workspaces_rls.sql"
    sql = restore_path.read_text(encoding="utf-8")

    corrupted = sql.replace(
        "COMMIT;",
        "CREATE TABLE ops.tampered_table (id uuid);\nCOMMIT;",
    )
    restore_path.write_text(corrupted, encoding="utf-8")

    errors = validator.validate_database_schema(migrations_dir)
    assert any("0003_restore_workspaces_rls.sql must not contain CREATE TABLE" in e for e in errors)
    assert any("differs from the reviewed authored SQL fingerprint" in e for e in errors)


@pytest.mark.parametrize("wrapper", ["comment", "block", "string", "dollar"])
@pytest.mark.parametrize("kind", ["policy", "trigger"])
def test_inactive_security_declaration_does_not_count(
    tmp_path: Path, wrapper: str, kind: str
) -> None:
    validator = _load_validator_module()
    migrations = _copy_migrations(tmp_path)
    path = migrations / "0001_baseline.sql"
    sql = path.read_text(encoding="utf-8")
    if kind == "policy":
        needle = next(
            line for line in sql.splitlines()
            if line.startswith("CREATE POLICY tenant_isolation ON iam.workspaces ")
        )
        expected = "CREATE POLICY tenant_isolation mismatch"
    else:
        needle = (
            "CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON governance.audit_events "
            "FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();"
        )
        expected = "Immutable trigger mismatch"
    wrappers = {
        "comment": "-- " + needle,
        "block": "/* outer /* nested comment */ " + needle + " */",
        "string": "SELECT '" + needle.replace("'", "''") + "';",
        "dollar": "SELECT $hidden$" + needle + "$hidden$;",
    }
    assert needle in sql
    path.write_text(sql.replace(needle, wrappers[wrapper], 1), encoding="utf-8")
    assert any(expected in error for error in validator.validate_database_schema(migrations))


def test_changed_dynamic_policy_predicate_requires_review(tmp_path: Path) -> None:
    validator = _load_validator_module()
    migrations = _copy_migrations(tmp_path)
    path = migrations / "0002_fail_closed_rls.sql"
    sql = path.read_text(encoding="utf-8")
    assert "AND workspace_id =" in sql
    path.write_text(sql.replace("AND workspace_id =", "OR workspace_id ="), encoding="utf-8")
    assert any(
        "differs from the reviewed authored SQL fingerprint" in error
        for error in validator.validate_database_schema(migrations)
    )
    assert validator.main(["--migrations-dir", str(migrations)]) == 1


def test_harmless_comments_and_quoted_ddl_are_not_executed(tmp_path: Path) -> None:
    validator = _load_validator_module()
    migrations = _copy_migrations(tmp_path)
    for path in migrations.glob("*.sql"):
        sql = path.read_text(encoding="utf-8")
        sql = "-- CREATE TABLE ignored; IS NULL; SUPERUSER; BYPASSRLS\n" + sql
        path.write_text(sql, encoding="utf-8")
    baseline = migrations / "0001_baseline.sql"
    sql = baseline.read_text(encoding="utf-8")
    baseline.write_text(
        sql + "\nSELECT 'a''b -- /*';\nSELECT $text$CREATE TABLE ops.ignored(id uuid);$text$;\n",
        encoding="utf-8",
    )
    assert validator.validate_database_schema(migrations) == []


@pytest.mark.parametrize("suffix", ["/* open", "SELECT 'open;", 'SELECT "open;', "DO $x$ open"])
def test_unterminated_sql_span_is_rejected(tmp_path: Path, suffix: str) -> None:
    validator = _load_validator_module()
    migrations = _copy_migrations(tmp_path)
    path = migrations / "0001_baseline.sql"
    path.write_text(path.read_text(encoding="utf-8") + suffix, encoding="utf-8")
    assert any("Unterminated" in error for error in validator.validate_database_schema(migrations))


def test_transaction_marker_in_comment_does_not_count(tmp_path: Path) -> None:
    validator = _load_validator_module()
    migrations = _copy_migrations(tmp_path)
    path = migrations / "0001_baseline.sql"
    sql = path.read_text(encoding="utf-8")
    assert "COMMIT;" in sql
    path.write_text(sql.replace("COMMIT;", "-- COMMIT;"), encoding="utf-8")
    assert any("explicit BEGIN; / COMMIT;" in e for e in validator.validate_database_schema(migrations))


@pytest.mark.parametrize("clause", ["SUPERUSER", "BYPASSRLS", "PASSWORD 'fixture-only'"])
def test_direct_privileged_or_password_clause_is_rejected(tmp_path: Path, clause: str) -> None:
    validator = _load_validator_module()
    migrations = _copy_migrations(tmp_path)
    path = migrations / "0001_baseline.sql"
    sql = path.read_text(encoding="utf-8")
    path.write_text(sql + f"\nCREATE ROLE fixture_user LOGIN {clause};\n", encoding="utf-8")
    assert any("forbidden direct" in e for e in validator.validate_database_schema(migrations))
