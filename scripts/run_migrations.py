"""Technical migration runner for the Enterprise AI Agent Platform.

Authority: DEC-DB-02 (APPROVED_BY_USER).
Manages technical table `public.schema_migrations` to record applied migration
versions, filenames, SHA-256 checksums, execution duration, and running identity.
Uses PostgreSQL advisory locks to guarantee single-runner concurrency safety,
detects checksum drift, stops on the first error, and runs migrations sequentially.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import psycopg
from validate_database_schema import SqlLexingError, _sql_structure

logger = logging.getLogger("migration_runner")

ADVISORY_LOCK_KEY: Final[str] = "enterprise_platform_migration_lock"
MIGRATION_FILENAME_RE: Final[re.Pattern[str]] = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

CREATE_HISTORY_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version text PRIMARY KEY,
    name text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    applied_by text NOT NULL,
    execution_time_ms integer NOT NULL
);
"""


class MigrationError(RuntimeError):
    """Base exception for all migration execution failures."""


class MigrationChecksumDriftError(MigrationError):
    """Raised when an already applied migration has a modified checksum."""


class MigrationExecutionError(MigrationError):
    """Raised when a migration fails during SQL execution."""


class MigrationLockError(MigrationError):
    """Raised when the migration lock cannot be acquired."""


class MigrationHistoryError(MigrationError):
    """An existing database cannot safely be matched to the supplied history."""


@dataclass(frozen=True, slots=True)
class MigrationFile:
    version: str
    seq: int
    name: str
    path: Path
    checksum: str
    sql: str


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: str
    name: str
    checksum: str
    applied_at: str
    applied_by: str
    execution_time_ms: int


def normalize_sql_for_checksum(content: str) -> str:
    """Normalize line breaks and BOM for deterministic SHA-256 calculation."""
    return content.replace("\r\n", "\n").strip()


def compute_migration_checksum(sql_text: str) -> str:
    normalized = normalize_sql_for_checksum(sql_text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def prepare_migration_sql(sql_text: str) -> str:
    """Remove only top-level transaction wrappers; the runner owns the commit.

    Reuse the static validator's tested lexer so comments, strings, quoted
    identifiers and dollar-quoted PL/pgSQL bodies remain verbatim. Never rewrite
    the immutable migration files or their checksums.
    """
    try:
        masked, _ = _sql_structure(sql_text)
    except SqlLexingError as exc:
        raise MigrationError(f"Cannot safely read migration SQL: {exc}") from exc
    result = list(sql_text)
    opened = False
    closed = False
    start = 0
    for end in [m.end() for m in re.finditer(";", masked)] + [len(masked)]:
        statement = masked[start:end].strip().rstrip(";").strip()
        words = statement.upper().split()
        if words and words[0] in {
            "BEGIN",
            "START",
            "COMMIT",
            "END",
            "ROLLBACK",
            "ABORT",
            "SAVEPOINT",
            "RELEASE",
            "PREPARE",
        }:
            if words in (
                ["BEGIN"],
                ["BEGIN", "WORK"],
                ["BEGIN", "TRANSACTION"],
                ["START", "TRANSACTION"],
            ):
                if opened or closed:
                    raise MigrationError(
                        "Nested or multiple migration transactions are unsupported"
                    )
                opened = True
            elif words in (["COMMIT"], ["COMMIT", "WORK"], ["COMMIT", "TRANSACTION"], ["END"]):
                if not opened or closed:
                    raise MigrationError("Unmatched migration COMMIT")
                closed = True
            else:
                raise MigrationError("Unsupported migration transaction control")
            # Preserve comments/quotes in this span; erase executable wrapper tokens only.
            for pos in range(start, end):
                if not masked[pos].isspace():
                    result[pos] = " "
        start = end
    if opened != closed:
        raise MigrationError("Unbalanced migration transaction wrapper")
    return "".join(result)


def write_bootstrap_sql(migrations_dir: Path, output: Path) -> None:
    """Generate an init-on-empty PostgreSQL script from the exact source chain.

    Both psql bootstrap and Python upgrades commit SQL plus history together.
    Generated output stays under ignored artifacts, never in the migration chain.
    """
    if output.resolve().is_relative_to(migrations_dir.resolve()):
        raise MigrationError("Generated bootstrap must not overwrite the canonical migration chain")
    migrations = discover_migration_files(migrations_dir)
    parts = [
        "-- GENERATED from canonical migrations. Empty PGDATA only; do not hand-edit.\n",
        "\\set ON_ERROR_STOP on\n",
        f"SELECT pg_advisory_lock(hashtext('{ADVISORY_LOCK_KEY}'));\n",
        CREATE_HISTORY_TABLE_SQL,
        "REVOKE ALL ON public.schema_migrations FROM PUBLIC, platform_app;\n",
    ]
    for migration in migrations:
        parts.extend(
            [
                "BEGIN;\n",
                prepare_migration_sql(migration.sql),
                "\n;\n",
                (
                    "INSERT INTO public.schema_migrations "
                    "(version,name,checksum,applied_by,execution_time_ms) VALUES "
                    f"('{migration.version}','{migration.name}','{migration.checksum}',current_user,0);\n"
                ),
                "COMMIT;\n",
            ]
        )
    parts.append(f"SELECT pg_advisory_unlock(hashtext('{ADVISORY_LOCK_KEY}'));\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(parts), encoding="utf-8", newline="\n")


def discover_migration_files(migrations_dir: Path) -> list[MigrationFile]:
    """Scan directory and return sorted, verified migration files."""
    if not migrations_dir.exists() or not migrations_dir.is_dir():
        raise MigrationError(f"Migrations directory does not exist: {migrations_dir}")

    files: list[MigrationFile] = []
    seen_seqs: dict[int, str] = {}

    for path in sorted(migrations_dir.glob("*.sql")):
        match = MIGRATION_FILENAME_RE.match(path.name)
        if match is None:
            continue
        version_str = match.group(1)
        seq = int(version_str)
        if seq in seen_seqs:
            raise MigrationError(
                f"Duplicate migration sequence {seq:04d}: '{seen_seqs[seq]}' and '{path.name}'"
            )
        seen_seqs[seq] = path.name

        sql_text = path.read_text(encoding="utf-8-sig")
        if not sql_text.strip():
            raise MigrationError(f"Migration file is empty: {path.name}")
        prepare_migration_sql(sql_text)

        checksum = compute_migration_checksum(sql_text)
        files.append(
            MigrationFile(
                version=version_str,
                seq=seq,
                name=path.name,
                path=path,
                checksum=checksum,
                sql=sql_text,
            )
        )

    # Ensure sequential numbering
    if files:
        ordered_seqs = [f.seq for f in files]
        expected_seqs = list(range(1, len(ordered_seqs) + 1))
        if ordered_seqs != expected_seqs:
            raise MigrationError(
                f"Non-sequential migration numbering: got {ordered_seqs}, expected {expected_seqs}"
            )

    if not files:
        raise MigrationError("No migration files found")
    return files


def ensure_history_table(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Create technical schema_migrations table if absent."""
    with conn.cursor() as cur:
        cur.execute(CREATE_HISTORY_TABLE_SQL)
        cur.execute("REVOKE ALL ON public.schema_migrations FROM PUBLIC, platform_app")


def get_applied_migrations(
    conn: psycopg.Connection[tuple[object, ...]],
) -> dict[str, AppliedMigration]:
    """Fetch applied migrations dictionary keyed by version."""
    applied: dict[str, AppliedMigration] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT version, name, checksum, applied_at::text, applied_by, execution_time_ms "
            "FROM public.schema_migrations ORDER BY version ASC"
        )
        for row in cur.fetchall():
            applied[str(row[0])] = AppliedMigration(
                version=str(row[0]),
                name=str(row[1]),
                checksum=str(row[2]),
                applied_at=str(row[3]),
                applied_by=str(row[4]),
                execution_time_ms=int(row[5]),
            )
    return applied


def acquire_advisory_lock(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Acquire advisory lock using 64-bit hashtext."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (ADVISORY_LOCK_KEY,))
        row = cur.fetchone()
        acquired = bool(row[0]) if row else False
        if not acquired:
            raise MigrationLockError(
                f"Could not acquire migration advisory lock for key '{ADVISORY_LOCK_KEY}'. "
                "Another migration runner may be executing."
            )


def release_advisory_lock(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Release advisory lock."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (ADVISORY_LOCK_KEY,))
    except psycopg.Error as exc:
        # Connection close also releases the session lock; preserve the original failure.
        logger.warning("Could not release migration lock: %s", type(exc).__name__)


def execute_migration_file(
    conn: psycopg.Connection[tuple[object, ...]],
    migration: MigrationFile,
) -> int:
    """Execute a single migration file in an isolated transaction and record it."""
    start_time = time.monotonic()
    with conn.cursor() as cur:
        cur.execute("SELECT current_user")
        row = cur.fetchone()
        current_user = str(row[0]) if row else "unknown"

    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(prepare_migration_sql(migration.sql))
            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            cur.execute(
                """
                INSERT INTO public.schema_migrations (
                    version, name, checksum, applied_at, applied_by, execution_time_ms
                ) VALUES (%s, %s, %s, now(), %s, %s)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    current_user,
                    elapsed_ms,
                ),
            )
        return elapsed_ms
    except Exception as exc:
        raise MigrationExecutionError(
            f"Failed to execute migration {migration.name}: {exc}"
        ) from exc


def run_migrations(
    database_url: str,
    migrations_dir: Path | None = None,
    *,
    dry_run: bool = False,
    check_only: bool = False,
) -> list[str]:
    """Run all pending migrations in order, detecting checksum drift and locking.

    Returns list of newly applied migration versions.
    """
    repo_root = Path(__file__).resolve().parents[1]
    target_dir = (
        migrations_dir if migrations_dir is not None else repo_root / "database" / "migrations"
    )
    migration_files = discover_migration_files(target_dir)

    with psycopg.connect(database_url, autocommit=True) as conn:
        acquire_advisory_lock(conn)
        try:
            history_exists = conn.execute(
                "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
            ).fetchone()[0]
            if not history_exists:
                existing_domain_table = conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = ANY(%s) AND table_type = 'BASE TABLE')",
                    (
                        [
                            "iam",
                            "catalog",
                            "knowledge",
                            "runtime",
                            "integration",
                            "governance",
                            "ops",
                        ],
                    ),
                ).fetchone()[0]
                if existing_domain_table:
                    raise MigrationHistoryError(
                        "Existing domain tables have no verified migration history. "
                        "Stop for reviewed adoption; do not replay baseline or reset the database."
                    )
            if not history_exists and not (check_only or dry_run):
                ensure_history_table(conn)
            applied = (
                get_applied_migrations(conn)
                if history_exists or not (check_only or dry_run)
                else {}
            )
            expected_prefix = [m.version for m in migration_files[: len(applied)]]
            if sorted(applied) != expected_prefix:
                raise MigrationHistoryError(
                    "Migration history is not a prefix of the supplied chain"
                )

            # 1. Verify checksums of already applied migrations
            for m in migration_files:
                if m.version in applied:
                    record = applied[m.version]
                    if record.name != m.name:
                        raise MigrationHistoryError(
                            f"Applied migration filename changed: {m.version}"
                        )
                    if record.checksum != m.checksum:
                        raise MigrationChecksumDriftError(
                            f"Migration {m.name} has checksum drift! "
                            f"Recorded: {record.checksum}, Current file: {m.checksum}. "
                            "Applied migrations must remain immutable."
                        )

            # 2. Identify pending migrations
            pending = [m for m in migration_files if m.version not in applied]

            if check_only:
                if pending:
                    raise MigrationError(
                        f"Unapplied pending migrations: {[p.name for p in pending]}"
                    )
                return []

            if not pending:
                logger.info("All %d migrations are up to date.", len(applied))
                return []

            if dry_run:
                logger.info(
                    "Dry-run: %d migrations pending: %s", len(pending), [p.name for p in pending]
                )
                return [p.version for p in pending]

            # 3. Apply pending migrations sequentially
            applied_now: list[str] = []
            for m in pending:
                logger.info("Applying migration %s...", m.name)
                elapsed_ms = execute_migration_file(conn, m)
                logger.info("Applied migration %s in %d ms.", m.name, elapsed_ms)
                applied_now.append(m.version)

            return applied_now
        finally:
            release_advisory_lock(conn)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(
        description="Enterprise AI Agent Platform Migration Runner (DEC-DB-02)"
    )
    parser.add_argument(
        "--write-bootstrap",
        type=Path,
        help="Generate SQL for empty Docker PGDATA without connecting to a database.",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL"),
        help="PostgreSQL connection URL for migration owner role.",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=None,
        help="Path to SQL migrations directory (default: database/migrations).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether any migrations are pending without applying them.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show pending migrations without applying.",
    )
    args = parser.parse_args(argv)

    if args.write_bootstrap:
        try:
            write_bootstrap_sql(
                args.migrations_dir or Path(__file__).resolve().parents[1] / "database/migrations",
                args.write_bootstrap,
            )
        except (MigrationError, OSError) as exc:
            logger.error("Bootstrap generation failed: %s", exc)
            return 1
        print("PASS: Generated transactional bootstrap SQL from the canonical migration chain.")
        return 0

    if not args.database_url:
        print(
            "ERROR: Database connection URL is required via --database-url or MIGRATION_DATABASE_URL/DATABASE_URL",
            file=sys.stderr,
        )
        return 1

    try:
        applied = run_migrations(
            args.database_url,
            args.migrations_dir,
            dry_run=args.dry_run,
            check_only=args.check,
        )
        if args.check:
            print("PASS: Database schema is completely up to date.")
        elif args.dry_run:
            print(f"PLAN: {len(applied)} migrations pending: {applied}")
        else:
            print(f"PASS: Applied {len(applied)} migrations successfully: {applied}")
        return 0
    except (MigrationError, psycopg.Error, OSError) as exc:
        logger.error("Migration failed: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
