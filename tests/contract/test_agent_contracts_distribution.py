"""Build and install real distributions without depending on a source checkout."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_NAME = "agent-spec.v1.schema.json"


def _run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def test_distributions_validate_outside_checkout(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "The project bootstrap requires uv"
    artifacts = tmp_path / "dist"
    # uv's default builds an sdist, then the wheel from that sdist.
    _run(
        [uv, "build", "--offline", "--package", "agent-contracts", "--out-dir", str(artifacts)],
        ROOT,
    )
    canonical = (ROOT / "contracts" / "jsonschema" / SCHEMA_NAME).read_bytes()
    wheels = list(artifacts.glob("*.whl"))
    sdists = list(artifacts.glob("*.tar.gz"))
    assert len(wheels) == len(sdists) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        assert archive.read(f"agent_contracts/schemas/{SCHEMA_NAME}") == canonical
    with tarfile.open(sdists[0]) as archive:
        member = next(m for m in archive.getmembers() if m.name.endswith(f"/schemas/{SCHEMA_NAME}"))
        schema_file = archive.extractfile(member)
        assert schema_file is not None
        with schema_file:
            assert schema_file.read() == canonical

    environment = tmp_path / "venv"
    _run([uv, "venv", "--python", sys.executable, str(environment)], tmp_path)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _run([uv, "pip", "install", "--offline", "--python", str(python), str(wheels[0])], tmp_path)
    env = os.environ.copy()
    env.pop("AGENT_SPEC_SCHEMA_PATH", None)
    # -I ignores PYTHONPATH, user site-packages, and the current directory.
    _run(
        [
            str(python),
            "-I",
            "-c",
            """
import hashlib
import importlib.resources
import pathlib
import sys
import agent_contracts
from agent_contracts import AgentSpecValidationError, validate_agent_spec_raw

assert not pathlib.Path(agent_contracts.__file__).is_relative_to(pathlib.Path(sys.argv[1]))
resource = importlib.resources.files('agent_contracts').joinpath('schemas/agent-spec.v1.schema.json')
assert hashlib.sha256(resource.read_bytes()).hexdigest() == sys.argv[2]
spec = {'schemaVersion': '1.0', 'kind': 'Agent', 'metadata': {'name': 'package-check'},
        'mode': 'fixed_plan', 'modelRef': 'unresolved', 'persona': {'instructions': 'Fixture'},
        'limits': {'maxSteps': 1}}
assert validate_agent_spec_raw(spec) == []
spec['workflowRef'] = None
try:
    validate_agent_spec_raw(spec)
except AgentSpecValidationError:
    pass
else:
    raise AssertionError('Installed canonical validator accepted an invalid null')
""",
            str(ROOT),
            hashlib.sha256(canonical).hexdigest(),
        ],
        tmp_path,
        env,
    )
