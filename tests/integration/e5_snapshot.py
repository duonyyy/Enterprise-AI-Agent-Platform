"""Locate provisioned pinned weights without machine-specific paths or downloads."""

import os
from pathlib import Path

from agent_runtime.knowledge.embeddings import EMBEDDING_REVISION


def provisioned_e5_snapshot() -> Path | None:
    if os.environ.get("E5_MODEL_SNAPSHOT"):
        # An explicit invalid path must not silently select another snapshot.
        return Path(os.environ["E5_MODEL_SNAPSHOT"])
    root = Path(__file__).resolve().parents[2]
    hf_root = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface")))
    hub = Path(os.environ.get("HF_HUB_CACHE", str(hf_root / "hub")))
    candidates = [
        root / "models/hub/models--intfloat--multilingual-e5-small/snapshots" / EMBEDDING_REVISION,
        hub / "models--intfloat--multilingual-e5-small/snapshots" / EMBEDDING_REVISION,
    ]
    return next((path for path in candidates if (path / "model.safetensors").is_file()), None)
