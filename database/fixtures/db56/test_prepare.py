"""Offline regression for fixture drift refusal and output integrity."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from prepare import PreparationError, check_outputs, verify_inputs, write_outputs


class FixturePreparationSafetyTests(unittest.TestCase):
    def test_normalized_schema_accepts_crlf_but_rejects_sql_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kit = root / "kit"
            kit.mkdir()
            migrations = root / "database" / "migrations"
            migrations.mkdir(parents=True)
            schema = migrations / "0001_baseline.sql"
            original = "-- fixture target\nCREATE TABLE example(id uuid);\n"
            schema.write_bytes(original.replace("\n", "\r\n").encode())
            provenance = {"reviewed_migrations": [{
                "path": "database/migrations/0001_baseline.sql",
                "normalized_sha256": hashlib.sha256(original.encode()).hexdigest(),
            }], "upstream_files": []}
            verify_inputs(root, kit, provenance)
            schema.write_text(original.replace("uuid", "text"), encoding="utf-8")
            with self.assertRaisesRegex(PreparationError, "Migration changed"):
                verify_inputs(root, kit, provenance)

    def test_new_migration_requires_compatibility_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            migrations = root / "database" / "migrations"
            migrations.mkdir(parents=True)
            (migrations / "0003_new.sql").write_text("SELECT 1;", encoding="utf-8")
            with self.assertRaisesRegex(PreparationError, "inventory changed"):
                verify_inputs(root, root, {"reviewed_migrations": [], "upstream_files": []})

    def test_reviewed_generator_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "generate_seed.py").write_bytes(b"modified generator")
            provenance = {"reviewed_migrations": [], "upstream_files": [{
                "path": "generate_seed.py", "sha256": hashlib.sha256(b"reviewed").hexdigest(),
            }]}
            with self.assertRaisesRegex(PreparationError, "missing or changed"):
                verify_inputs(root, root, provenance)

    def test_output_check_rejects_sql_tampering_without_replacing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            target = destination / "seed_small.sql"
            target.write_bytes(b"tampered SQL")
            with self.assertRaisesRegex(PreparationError, "missing or changed"):
                check_outputs(destination, {"seed_small.sql": b"reviewed SQL"})
            self.assertEqual(target.read_bytes(), b"tampered SQL")

    def test_missing_output_is_not_reported_as_verified(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp,
            self.assertRaisesRegex(PreparationError, "missing or changed"),
        ):
            check_outputs(Path(temp), {"manifest_small.json": json.dumps({}).encode()})

    def test_output_outside_artifact_root_is_rejected_before_creating_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "unrelated-output"
            with self.assertRaisesRegex(PreparationError, "outside"):
                write_outputs(destination, {"seed_small.sql": b"SELECT 1;"})
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
