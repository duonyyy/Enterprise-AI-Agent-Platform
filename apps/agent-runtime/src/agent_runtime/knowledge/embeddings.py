"""Pinned, offline multilingual E5 inference; no synthetic vectors or network fallback."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from threading import RLock
from typing import Final

EMBEDDING_MODEL: Final = "intfloat/multilingual-e5-small"
EMBEDDING_REVISION: Final = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
EMBEDDING_DIMENSIONS = 384
MAX_EMBEDDING_TOKENS = 512
MAX_EMBEDDING_BATCH = 32
MAX_TEXT_BYTES = 32_768


class EmbeddingUnavailable(RuntimeError):
    """A real model could not be loaded or produce valid embeddings."""


class E5EmbeddingModel:
    """Use an explicitly provisioned snapshot; never retain a corpus or fetch weights online."""

    def __init__(self, snapshot_path: Path, *, device: str = "cpu") -> None:
        snapshot = snapshot_path.resolve()
        if snapshot.name != EMBEDDING_REVISION:
            raise ValueError("embedding_snapshot_revision_mismatch")
        if not all((snapshot / name).is_file() for name in ("config.json", "model.safetensors")):
            raise EmbeddingUnavailable("embedding_snapshot_not_provisioned")
        if device not in {"cpu", "cuda"}:
            raise ValueError("unsupported_embedding_device")
        # Optional dependency: unrelated API startup must not download/load a model.
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                str(snapshot), device=device, local_files_only=True, trust_remote_code=False
            )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            raise EmbeddingUnavailable("embedding_model_load_failed") from exc
        if self._model.get_embedding_dimension() != EMBEDDING_DIMENSIONS:
            raise EmbeddingUnavailable("embedding_model_dimension_mismatch")
        self._model.max_seq_length = MAX_EMBEDDING_TOKENS
        self._lock = RLock()

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._encode((text,), prefix="query: ")[0]

    def embed_passages(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return self._encode(texts, prefix="passage: ")

    def _encode(self, texts: Sequence[str], *, prefix: str) -> tuple[tuple[float, ...], ...]:
        if isinstance(texts, (str, bytes)) or not texts or len(texts) > MAX_EMBEDDING_BATCH:
            raise ValueError("embedding_batch_size_out_of_range")
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("embedding_text_empty")
            if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
                raise ValueError("embedding_text_too_large")
        inputs = [prefix + text for text in texts]
        with self._lock:
            tokens = self._model.tokenizer(inputs, truncation=False, padding=False)["input_ids"]
            if any(len(row) > MAX_EMBEDDING_TOKENS for row in tokens):
                raise ValueError("embedding_token_limit_exceeded")
            try:
                vectors = self._model.encode(
                    inputs,
                    batch_size=8,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise EmbeddingUnavailable("embedding_inference_failed") from exc
        if vectors.shape != (len(texts), EMBEDDING_DIMENSIONS):
            raise EmbeddingUnavailable("embedding_response_shape_invalid")
        result = tuple(tuple(float(value) for value in row) for row in vectors)
        for row in result:
            norm = math.sqrt(sum(value * value for value in row))
            if not all(math.isfinite(value) for value in row) or not math.isclose(
                norm, 1.0, abs_tol=1e-4
            ):
                raise EmbeddingUnavailable("embedding_response_vector_invalid")
        return result
