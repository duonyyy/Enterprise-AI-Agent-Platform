#!/usr/bin/env python3
"""Prepare/check synthetic DB56 SQL offline. Never connects to a database."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from generate_seed import PROFILE, make

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUTPUT = ROOT / "artifacts" / "sample-data" / "db56"


class PreparationError(ValueError):
    """The reviewed fixture inputs or prepared outputs no longer match."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_inputs(root: Path, kit: Path, provenance: dict) -> None:
    expected = provenance["reviewed_migrations"]
    actual_paths = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "database" / "migrations").glob("*.sql")
    )
    if actual_paths != sorted(item["path"] for item in expected):
        raise PreparationError("Migration inventory changed; review fixture compatibility first.")
    for item in expected:
        path = root / item["path"]
        normalized = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        if sha256(normalized.encode("utf-8")) != item["normalized_sha256"]:
            raise PreparationError(f"Migration changed: {item['path']}; compatibility review required.")
    selected = {"generate_seed.py", "verify_table_counts.sql"}
    for item in provenance["upstream_files"]:
        name = item["path"]
        if name not in selected and not name.startswith("fixtures/"):
            continue
        path = kit / name
        if not path.is_file() or sha256(path.read_bytes()) != item["sha256"]:
            raise PreparationError(f"Reviewed upstream file missing or changed: {name}")


def render(profile: str, root: Path, provenance: dict) -> dict[str, bytes]:
    sql, manifest = make(profile, root / "database" / "migrations" / "0001_baseline.sql")
    counts = manifest["counts_per_table"]
    if len(counts) != 56 or sum(counts.values()) != manifest["generated_rows"]:
        raise PreparationError(f"Unexpected fixture inventory for {profile}.")
    if sql.count("ON CONFLICT (id) DO NOTHING;") != manifest["generated_rows"]:
        raise PreparationError(f"Unexpected INSERT count for {profile}.")
    if sql.count("\nBEGIN;") != 1 or sql.count("\nCOMMIT;") != 1:
        raise PreparationError("Expected one fixture transaction.")
    if "Refusing demo seed" not in sql or "app.allow_demo_seed" not in sql:
        raise PreparationError("Missing fixture import opt-in guard.")
    sql_bytes = sql.encode("utf-8")
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    metadata = {
        "profile": profile,
        "synthetic_fixtures_only": True,
        "database_import": "NOT_RUN",
        "runtime_execution_verified": False,
        "archive_sha256": provenance["archive_sha256"],
        "reviewed_migrations": provenance["reviewed_migrations"],
        "generated_rows": manifest["generated_rows"],
        "table_count": len(counts),
        "sql_sha256": sha256(sql_bytes),
        "manifest_sha256": sha256(manifest_bytes),
    }
    return {
        f"seed_{profile}.sql": sql_bytes,
        f"manifest_{profile}.json": manifest_bytes,
        "PREPARATION.json": (json.dumps(metadata, indent=2) + "\n").encode("utf-8"),
    }


def check_outputs(destination: Path, expected: dict[str, bytes]) -> None:
    for name, data in expected.items():
        path = destination / name
        if not path.is_file() or path.read_bytes() != data:
            raise PreparationError(f"Prepared output missing or changed: {path}")


def write_outputs(destination: Path, expected: dict[str, bytes]) -> None:
    # Do not follow a redirected output folder or file outside our dedicated artifact root.
    if not destination.resolve().is_relative_to(OUTPUT):
        raise PreparationError("Output directory resolves outside the sample-data artifact root.")
    destination.mkdir(parents=True, exist_ok=True)
    for name in expected:
        target = destination / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise PreparationError(f"Refusing redirected/non-file output: {target}")
    for name, data in expected.items():
        (destination / name).write_bytes(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=[*PROFILE, "all"], default="small")
    parser.add_argument("--check", action="store_true", help="Verify existing outputs without writing.")
    args = parser.parse_args(argv)
    try:
        provenance = json.loads((HERE / "UPSTREAM.json").read_text(encoding="utf-8"))
        verify_inputs(ROOT, HERE, provenance)
        profiles = list(PROFILE) if args.profile == "all" else [args.profile]
        # Generate all selected profiles before replacing any prepared output.
        prepared = {profile: render(profile, ROOT, provenance) for profile in profiles}
        for profile, expected in prepared.items():
            destination = OUTPUT / profile
            if args.check:
                check_outputs(destination, expected)
            else:
                write_outputs(destination, expected)
            manifest = json.loads(expected[f"manifest_{profile}.json"])
            label = "PASS offline check" if args.check else "PREPARED"
            sys.stdout.write(f"{label}: {profile}, {manifest['generated_rows']} synthetic rows, 56 tables.\n")
        sys.stdout.write("No database/provider/object-store/vector-index connection was made.\n")
        return 0
    except (OSError, ValueError, AssertionError, KeyError) as exc:
        sys.stderr.write(f"FAIL fixture preparation: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
