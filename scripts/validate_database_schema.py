"""Static schema and migration validator for the DB-56 PostgreSQL baseline.

This script runs in public-only checkouts and CI without requiring private
documentation bundles (`docs/`) or a live PostgreSQL database connection.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

EXPECTED_MIGRATIONS: Final[tuple[str, ...]] = (
    "0001_baseline.sql",
    "0002_fail_closed_rls.sql",
    "0003_restore_workspaces_rls.sql",
)

# Pin the reviewed authored program, not its database effects. Whitespace and
# comments outside quoted bodies are ignored; PL/pgSQL bodies remain opaque.
REVIEWED_POLICY_FINGERPRINT: Final[str] = (
    "0b176f7075846f6424c795293a122d538a6f23ad4bccab89c12508f4237e93b1"
)
REVIEWED_RESTORE_RLS_FINGERPRINT: Final[str] = (
    "e078a8dadd4a793ed6237ce6402f7ff2fdb23cfc421feeb9ff722af013fb9a22"
)
DOLLAR_QUOTE_RE: Final[re.Pattern[str]] = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")
WORD_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*|[0-9]+")


class SqlLexingError(ValueError):
    """SQL cannot be safely separated into executable text and opaque spans."""


def _sql_structure(sql: str) -> tuple[str, str]:
    """Mask comments/quoted spans and fingerprint all non-comment tokens.

    This is a lexer for the baseline's declarations, not a PostgreSQL parser.
    Strings, identifiers and dollar-quoted function/DO bodies never count as
    top-level DDL. Unterminated spans are rejected rather than partially read.
    """
    if "\x00" in sql:
        raise SqlLexingError("NUL byte in SQL")
    masked = list(sql)
    tokens: list[str] = []
    pos = 0

    def hide(start: int, end: int) -> None:
        for index in range(start, end):
            if masked[index] not in "\r\n":
                masked[index] = " "

    while pos < len(sql):
        start = pos
        if sql[pos].isspace():
            pos += 1
            continue
        if sql.startswith("--", pos):
            end = sql.find("\n", pos)
            pos = len(sql) if end < 0 else end
            hide(start, pos)
            continue
        if sql.startswith("/*", pos):
            depth = 1
            pos += 2
            while pos < len(sql) and depth:
                if sql.startswith("/*", pos):
                    depth += 1
                    pos += 2
                elif sql.startswith("*/", pos):
                    depth -= 1
                    pos += 2
                else:
                    pos += 1
            if depth:
                raise SqlLexingError("Unterminated block comment")
            hide(start, pos)
            continue
        if sql[pos] in "\"'":
            quote = sql[pos]
            escaped = (
                quote == "'" and pos > 0 and sql[pos - 1] in "eE"
                and (pos < 2 or not (sql[pos - 2].isalnum() or sql[pos - 2] == "_"))
            )
            pos += 1
            while pos < len(sql):
                if escaped and sql[pos] == "\\":
                    pos += 2
                elif sql[pos] == quote:
                    pos += 1
                    if pos < len(sql) and sql[pos] == quote:
                        pos += 1
                        continue
                    break
                else:
                    pos += 1
            else:
                raise SqlLexingError("Unterminated quoted SQL span")
            tokens.append(sql[start:pos])
            hide(start, pos)
            continue
        dollar = DOLLAR_QUOTE_RE.match(sql, pos)
        if dollar is not None:
            delimiter = dollar.group()
            end = sql.find(delimiter, dollar.end())
            if end < 0:
                raise SqlLexingError("Unterminated dollar-quoted SQL span")
            pos = end + len(delimiter)
            tokens.append(sql[start:pos])
            hide(start, pos)
            continue
        word = WORD_RE.match(sql, pos)
        pos = word.end() if word is not None else pos + 1
        tokens.append(sql[start:pos])
    fingerprint = hashlib.sha256("\x00".join(tokens).encode("utf-8")).hexdigest()
    return "".join(masked), fingerprint

EXPECTED_SCHEMAS: Final[tuple[str, ...]] = (
    "iam",
    "catalog",
    "knowledge",
    "runtime",
    "integration",
    "governance",
    "ops",
)

EXPECTED_SCHEMA_TABLE_COUNTS: Final[dict[str, int]] = {
    "iam": 5,
    "catalog": 16,
    "knowledge": 7,
    "runtime": 12,
    "integration": 6,
    "governance": 4,
    "ops": 6,
}

EXPECTED_TABLES: Final[frozenset[str]] = frozenset(
    {
        # iam (5)
        "iam.tenants",
        "iam.workspaces",
        "iam.users",
        "iam.memberships",
        "iam.service_accounts",
        # catalog (16)
        "catalog.model_configs",
        "catalog.workflows",
        "catalog.workflow_versions",
        "catalog.tools",
        "catalog.tool_versions",
        "catalog.tool_grants",
        "catalog.agents",
        "catalog.agent_versions",
        "catalog.agent_tool_bindings",
        "catalog.agent_knowledge_bindings",
        "catalog.agent_publications",
        "catalog.digital_workers",
        "catalog.business_processes",
        "catalog.skills",
        "catalog.skill_versions",
        "catalog.agent_skill_bindings",
        # knowledge (7)
        "knowledge.bases",
        "knowledge.base_grants",
        "knowledge.documents",
        "knowledge.document_versions",
        "knowledge.ingestion_jobs",
        "knowledge.chunks",
        "knowledge.vector_refs",
        # runtime (12)
        "runtime.conversations",
        "runtime.messages",
        "runtime.runs",
        "runtime.run_steps",
        "runtime.run_events",
        "runtime.tool_operations",
        "runtime.approval_requests",
        "runtime.approval_decisions",
        "runtime.checkpoint_refs",
        "runtime.memory_records",
        "runtime.work_items",
        "runtime.jobs",
        # integration (6)
        "integration.connections",
        "integration.outbox_events",
        "integration.inbox_receipts",
        "integration.mcp_discovery_snapshots",
        "integration.mcp_discovered_items",
        "integration.mcp_tool_mappings",
        # governance (4)
        "governance.policy_sets",
        "governance.policy_versions",
        "governance.audit_events",
        "governance.retention_policies",
        # ops (6)
        "ops.model_usage",
        "ops.eval_datasets",
        "ops.eval_dataset_versions",
        "ops.eval_cases",
        "ops.eval_runs",
        "ops.eval_results",
    }
)

# Normalize case-sensitive canonical lowercase names.
CANONICAL_TABLES: Final[frozenset[str]] = frozenset(name.lower() for name in EXPECTED_TABLES)

GLOBAL_TABLES: Final[frozenset[str]] = frozenset({"iam.users"})
TENANT_ROOT_TABLES: Final[frozenset[str]] = frozenset({"iam.tenants"})
TENANT_SCOPED_TABLES: Final[frozenset[str]] = frozenset({"iam.workspaces"})

EXPECTED_IMMUTABLE_TRIGGER_TABLES: Final[frozenset[str]] = frozenset(
    {
        "catalog.workflow_versions",
        "catalog.tool_versions",
        "catalog.agent_versions",
        "governance.policy_versions",
        "governance.audit_events",
        "runtime.approval_decisions",
        "catalog.skill_versions",
        "catalog.agent_skill_bindings",
        "integration.mcp_discovery_snapshots",
        "integration.mcp_discovered_items",
    }
)

MIGRATION_FILENAME_RE: Final[re.Pattern[str]] = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
CREATE_SCHEMA_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+([a-z_][a-z0-9_]*)\s*;",
    re.MULTILINE,
)
CREATE_TABLE_RE: Final[re.Pattern[str]] = re.compile(
    r"CREATE\s+TABLE\s+([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\s*\((.*?)\n\);",
    re.DOTALL,
)
COLUMN_DEF_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s+([a-z][a-z_0-9]*)\s+"
    r"(?:uuid|text|bytea|integer|bigint|timestamptz|numeric\([^)]+\)|jsonb|char\([^)]+\)|boolean)\b",
    re.MULTILINE,
)
INLINE_REF_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s+([a-z][a-z_0-9]*)\s+[^\n]*?\bREFERENCES\s+([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\s*\(([^)]+)\)",
    re.MULTILINE,
)
TABLE_FK_RE: Final[re.Pattern[str]] = re.compile(
    r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\s*\(([^)]+)\)",
    re.DOTALL,
)
UNIQUE_RE: Final[re.Pattern[str]] = re.compile(r"UNIQUE\s*\(([^)]+)\)")
ENABLE_RLS_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*ALTER\s+TABLE\s+([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY\s*;",
    re.MULTILINE,
)
FORCE_RLS_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*ALTER\s+TABLE\s+([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\s+FORCE\s+ROW\s+LEVEL\s+SECURITY\s*;",
    re.MULTILINE,
)
CREATE_POLICY_RE: Final[re.Pattern[str]] = re.compile(
    r"CREATE\s+POLICY\s+tenant_isolation\s+ON\s+([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\b",
)
IMMUTABLE_TRIGGER_RE: Final[re.Pattern[str]] = re.compile(
    r"CREATE\s+TRIGGER\s+immutable_row\s+BEFORE\s+UPDATE\s+OR\s+DELETE\s+ON\s+"
    r"([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\s+FOR\s+EACH\s+ROW\s+EXECUTE\s+FUNCTION\s+"
    r"governance\.reject_immutable_mutation\(\)\s*;",
)
DOC_VERSION_TRIGGER_RE: Final[re.Pattern[str]] = re.compile(
    r"CREATE\s+TRIGGER\s+guard_document_version_before_update\s+BEFORE\s+UPDATE\s+ON\s+"
    r"knowledge\.document_versions\s+FOR\s+EACH\s+ROW\s+EXECUTE\s+FUNCTION\s+"
    r"governance\.guard_document_version\(\)\s*;",
)
FORBIDDEN_DDL_IN_POLICY_MIGRATION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:CREATE|DROP)\s+TABLE\b",
    re.IGNORECASE,
)
FORBIDDEN_ROLE_PRIVILEGE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:SUPERUSER|BYPASSRLS|PASSWORD)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedTable:
    """Parsed metadata for a single `CREATE TABLE` block."""

    name: str
    schema: str
    body: str
    offset: int
    columns: frozenset[str]
    unique_tuples: tuple[tuple[str, ...], ...]


def _split_columns(raw: str) -> tuple[str, ...]:
    return tuple(col.strip() for col in raw.split(",") if col.strip())


def _validate_migration_chain(migrations_dir: Path) -> list[str]:
    errors: list[str] = []
    if not migrations_dir.exists() or not migrations_dir.is_dir():
        return [f"Migrations directory does not exist: {migrations_dir}"]

    sql_files = sorted(path.name for path in migrations_dir.glob("*.sql"))
    if not sql_files:
        return [f"No SQL migration files found in: {migrations_dir}"]

    seen_numbers: dict[int, str] = {}
    for filename in sql_files:
        match = MIGRATION_FILENAME_RE.match(filename)
        if match is None:
            errors.append(
                f"Invalid migration filename '{filename}' (expected NNNN_snake_case.sql)"
            )
            continue
        seq = int(match.group(1))
        if seq in seen_numbers:
            errors.append(
                f"Duplicate migration sequence prefix {seq:04d}: "
                f"'{seen_numbers[seq]}' and '{filename}'"
            )
        seen_numbers[seq] = filename

    if seen_numbers:
        ordered_seqs = sorted(seen_numbers)
        expected_seqs = list(range(1, len(ordered_seqs) + 1))
        if ordered_seqs != expected_seqs:
            errors.append(
                f"Non-sequential migration numbering: got {ordered_seqs}, expected {expected_seqs}"
            )

    for required in EXPECTED_MIGRATIONS:
        file_path = migrations_dir / required
        if not file_path.is_file():
            errors.append(f"Missing required canonical migration file: {required}")

    extra_files = sorted(set(sql_files) - set(EXPECTED_MIGRATIONS))
    if extra_files:
        errors.append(
            f"Unexpected unapproved migration files in baseline chain: {extra_files}"
        )

    for filename in sql_files:
        file_path = migrations_dir / filename
        if not file_path.is_file():
            continue
        content = file_path.read_text(encoding="utf-8-sig")
        if not content.strip():
            errors.append(f"Migration file '{filename}' is empty")
            continue
        try:
            executable, _ = _sql_structure(content)
        except SqlLexingError as exc:
            errors.append(f"Migration file '{filename}': {exc}")
            continue
        if not re.search(r"\bBEGIN\s*;", executable) or not re.search(
            r"\bCOMMIT\s*;", executable
        ):
            errors.append(
                f"Migration file '{filename}' must wrap DDL inside explicit BEGIN; / COMMIT;"
            )
        if FORBIDDEN_ROLE_PRIVILEGE_RE.search(executable):
            errors.append(
                f"Migration file '{filename}' contains a forbidden direct "
                "SUPERUSER/BYPASSRLS/PASSWORD clause"
            )

    return errors


def _parse_tables(sql: str) -> tuple[dict[str, ParsedTable], list[str]]:
    errors: list[str] = []
    tables: dict[str, ParsedTable] = {}

    for match in CREATE_TABLE_RE.finditer(sql):
        table_name = match.group(1)
        body = match.group(2)
        offset = match.start()
        if table_name in tables:
            errors.append(f"Duplicate CREATE TABLE declaration for {table_name}")
            continue

        schema = table_name.split(".", 1)[0]
        columns = frozenset(col_match.group(1) for col_match in COLUMN_DEF_RE.finditer(body))
        unique_list: list[tuple[str, ...]] = [("id",)] if "id uuid PRIMARY KEY" in body else []
        for uniq_match in UNIQUE_RE.finditer(body):
            unique_list.append(_split_columns(uniq_match.group(1)))

        tables[table_name] = ParsedTable(
            name=table_name,
            schema=schema,
            body=body,
            offset=offset,
            columns=columns,
            unique_tuples=tuple(unique_list),
        )

    return tables, errors


def _validate_baseline_sql(sql: str) -> list[str]:
    errors: list[str] = []

    # These are recognized declaration/structure checks, never semantic proof
    # of grants, trigger bodies or an effective policy after migrations.
    if "rolname = 'platform_app'" not in sql or "GRANT SELECT, INSERT, UPDATE, DELETE" not in sql:
        errors.append("0001_baseline.sql is missing the baseline role/grant text fragments")
    sql, _ = _sql_structure(sql)

    declared_schemas = tuple(CREATE_SCHEMA_RE.findall(sql))
    if set(declared_schemas) != set(EXPECTED_SCHEMAS) or len(declared_schemas) != len(
        EXPECTED_SCHEMAS
    ):
        errors.append(
            f"Expected 7 canonical schemas {list(EXPECTED_SCHEMAS)}, found {list(declared_schemas)}"
        )

    tables, parse_errors = _parse_tables(sql)
    errors.extend(parse_errors)

    if len(tables) != len(CANONICAL_TABLES):
        errors.append(f"Expected {len(CANONICAL_TABLES)} tables, got {len(tables)}")

    missing_tables = sorted(CANONICAL_TABLES - set(tables))
    extra_tables = sorted(set(tables) - CANONICAL_TABLES)
    if missing_tables:
        errors.append(f"Missing canonical tables in 0001_baseline.sql: {missing_tables}")
    if extra_tables:
        errors.append(f"Unexpected non-canonical tables in 0001_baseline.sql: {extra_tables}")

    schema_counts: dict[str, int] = {schema: 0 for schema in EXPECTED_SCHEMAS}
    for table in tables.values():
        schema_counts[table.schema] = schema_counts.get(table.schema, 0) + 1

    for schema, expected_count in EXPECTED_SCHEMA_TABLE_COUNTS.items():
        actual_count = schema_counts.get(schema, 0)
        if actual_count != expected_count:
            errors.append(
                f"Schema '{schema}' table count mismatch: expected {expected_count}, got {actual_count}"
            )

    for name, table in tables.items():
        if "id" not in table.columns or "id uuid PRIMARY KEY" not in table.body:
            errors.append(f"{name}: missing 'id uuid PRIMARY KEY'")

        if name in GLOBAL_TABLES:
            continue
        if name in TENANT_ROOT_TABLES:
            continue
        if name in TENANT_SCOPED_TABLES:
            if "tenant_id" not in table.columns:
                errors.append(f"{name}: missing required column 'tenant_id'")
            if ("tenant_id", "id") not in table.unique_tuples:
                errors.append(f"{name}: missing required UNIQUE (tenant_id, id)")
            continue

        # All remaining 53 tables are workspace-scoped.
        for required_col in ("tenant_id", "workspace_id"):
            if required_col not in table.columns:
                errors.append(f"{name}: missing required scope column '{required_col}'")
        if ("tenant_id", "workspace_id", "id") not in table.unique_tuples:
            errors.append(
                f"{name}: missing composite tenant/workspace identity UNIQUE (tenant_id, workspace_id, id)"
            )

    # Validate inline and table-level foreign keys.
    for name, table in tables.items():
        fk_specs: list[tuple[tuple[str, ...], str, tuple[str, ...]]] = []
        for inline_match in INLINE_REF_RE.finditer(table.body):
            src_col = inline_match.group(1)
            target_table = inline_match.group(2)
            target_cols = _split_columns(inline_match.group(3))
            fk_specs.append(((src_col,), target_table, target_cols))

        for fk_match in TABLE_FK_RE.finditer(table.body):
            src_cols = _split_columns(fk_match.group(1))
            target_table = fk_match.group(2)
            target_cols = _split_columns(fk_match.group(3))
            fk_specs.append((src_cols, target_table, target_cols))

        if name not in (GLOBAL_TABLES | TENANT_ROOT_TABLES | TENANT_SCOPED_TABLES):
            has_workspace_fk = any(
                src == ("tenant_id", "workspace_id")
                and target == "iam.workspaces"
                and tgt_cols == ("tenant_id", "id")
                for src, target, tgt_cols in fk_specs
            )
            if not has_workspace_fk:
                errors.append(
                    f"{name}: missing FOREIGN KEY (tenant_id, workspace_id) "
                    "REFERENCES iam.workspaces(tenant_id, id)"
                )

        for src_cols, target_name, target_cols in fk_specs:
            for src_col in src_cols:
                if src_col not in table.columns:
                    errors.append(
                        f"{name}: foreign key references undeclared local column '{src_col}'"
                    )
            if target_name not in tables:
                errors.append(f"{name}: missing reference table {target_name}")
                continue

            target_table = tables[target_name]
            if target_table.offset > table.offset:
                errors.append(f"{name}: forward reference to {target_name}")

            if len(src_cols) != len(target_cols):
                errors.append(
                    f"{name}: foreign key column count mismatch on {target_name}: "
                    f"{list(src_cols)} -> {list(target_cols)}"
                )

            for target_col in target_cols:
                if target_col not in target_table.columns:
                    errors.append(
                        f"{name}: target {target_name} missing referenced column '{target_col}'"
                    )

            if target_cols not in target_table.unique_tuples:
                errors.append(
                    f"{name}: target {target_name} missing UNIQUE/PK {list(target_cols)}"
                )

    # Validate RLS declarations across all 55 non-global tables.
    expected_rls_tables = CANONICAL_TABLES - GLOBAL_TABLES
    enabled_rls = set(ENABLE_RLS_RE.findall(sql))
    forced_rls = set(FORCE_RLS_RE.findall(sql))
    policy_tables = set(CREATE_POLICY_RE.findall(sql))

    if len(ENABLE_RLS_RE.findall(sql)) != 55 or enabled_rls != expected_rls_tables:
        missing = sorted(expected_rls_tables - enabled_rls)
        extra = sorted(enabled_rls - expected_rls_tables)
        errors.append(
            f"ENABLE ROW LEVEL SECURITY mismatch (expected 55 non-global tables): "
            f"missing={missing}, extra={extra}"
        )

    if len(FORCE_RLS_RE.findall(sql)) != 55 or forced_rls != expected_rls_tables:
        missing = sorted(expected_rls_tables - forced_rls)
        extra = sorted(forced_rls - expected_rls_tables)
        errors.append(
            f"FORCE ROW LEVEL SECURITY mismatch (expected 55 non-global tables): "
            f"missing={missing}, extra={extra}"
        )

    if len(CREATE_POLICY_RE.findall(sql)) != 55 or policy_tables != expected_rls_tables:
        missing = sorted(expected_rls_tables - policy_tables)
        extra = sorted(policy_tables - expected_rls_tables)
        errors.append(
            f"CREATE POLICY tenant_isolation mismatch (expected 55 non-global tables): "
            f"missing={missing}, extra={extra}"
        )

    # Validate immutable triggers and document version guard trigger.
    immutable_triggers = set(IMMUTABLE_TRIGGER_RE.findall(sql))
    if (
        len(IMMUTABLE_TRIGGER_RE.findall(sql)) != len(EXPECTED_IMMUTABLE_TRIGGER_TABLES)
        or immutable_triggers != EXPECTED_IMMUTABLE_TRIGGER_TABLES
    ):
        missing = sorted(EXPECTED_IMMUTABLE_TRIGGER_TABLES - immutable_triggers)
        extra = sorted(immutable_triggers - EXPECTED_IMMUTABLE_TRIGGER_TABLES)
        errors.append(
            f"Immutable trigger mismatch (expected {len(EXPECTED_IMMUTABLE_TRIGGER_TABLES)} tables): "
            f"missing={missing}, extra={extra}"
        )

    if len(DOC_VERSION_TRIGGER_RE.findall(sql)) != 1:
        errors.append(
            "Missing or duplicate guard_document_version_before_update trigger on knowledge.document_versions"
        )

    return errors


def _validate_fail_closed_rls_sql(sql: str) -> list[str]:
    errors: list[str] = []
    executable, fingerprint = _sql_structure(sql)

    if FORBIDDEN_DDL_IN_POLICY_MIGRATION_RE.search(executable):
        errors.append("0002_fail_closed_rls.sql must not contain CREATE TABLE or DROP TABLE")

    if fingerprint != REVIEWED_POLICY_FINGERPRINT:
        errors.append(
            "0002_fail_closed_rls.sql differs from the reviewed authored SQL fingerprint; "
            "dynamic policy changes are unsupported without review, not semantically validated"
        )

    return errors


def _validate_restore_workspaces_rls_sql(sql: str) -> list[str]:
    errors: list[str] = []
    executable, fingerprint = _sql_structure(sql)

    if FORBIDDEN_DDL_IN_POLICY_MIGRATION_RE.search(executable):
        errors.append("0003_restore_workspaces_rls.sql must not contain CREATE TABLE or DROP TABLE")

    if fingerprint != REVIEWED_RESTORE_RLS_FINGERPRINT:
        errors.append(
            "0003_restore_workspaces_rls.sql differs from the reviewed authored SQL fingerprint; "
            "policy changes require review"
        )

    if not re.search(r"CREATE\s+POLICY\s+tenant_isolation\s+ON\s+iam\.workspaces\b", executable):
        errors.append(
            "0003_restore_workspaces_rls.sql must declare CREATE POLICY tenant_isolation ON iam.workspaces"
        )

    return errors


def validate_database_schema(migrations_dir: Path | None = None) -> list[str]:
    """Validate the canonical SQL migration directory and return a list of errors."""
    repo_root = Path(__file__).resolve().parents[1]
    target_dir = migrations_dir if migrations_dir is not None else repo_root / "database" / "migrations"

    errors = _validate_migration_chain(target_dir)
    if errors:
        return errors

    baseline_sql = (target_dir / "0001_baseline.sql").read_text(encoding="utf-8-sig")
    errors.extend(_validate_baseline_sql(baseline_sql))

    rls_sql = (target_dir / "0002_fail_closed_rls.sql").read_text(encoding="utf-8-sig")
    errors.extend(_validate_fail_closed_rls_sql(rls_sql))

    restore_sql = (target_dir / "0003_restore_workspaces_rls.sql").read_text(encoding="utf-8-sig")
    errors.extend(_validate_restore_workspaces_rls_sql(restore_sql))

    return errors


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for static database schema validation."""
    parser = argparse.ArgumentParser(
        description="Validate static DB-56 PostgreSQL schema and migration invariants."
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=None,
        help="Optional path to the directory containing SQL migrations.",
    )
    args = parser.parse_args(argv)

    errors = validate_database_schema(args.migrations_dir)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(
        "PASS static checks: 3 ordered migrations, 7 schemas, 56 canonical tables, "
        "recognized FK/UNIQUE declarations, 55 baseline RLS declarations, "
        "10 immutable trigger declarations + 1 document guard, "
        "reviewed 0002 content fingerprint, reviewed 0003 content fingerprint."
    )
    print(
        "0003_restore_workspaces_rls.sql restores iam.workspaces tenant_isolation policy (DEC-DB-01)."
    )
    print(
        "NOT_RUN: actual PostgreSQL DDL execution, dynamic PL/pgSQL policy state, "
        "live RLS isolation, and crash/recovery testing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
