"""Real E5 embedding adapter integration tests with offline weights (G5-13, G5-14)."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from uuid import uuid4

import pytest
from agent_contracts.model_contracts import ModelEmbeddingRequest
from agent_runtime.knowledge.embeddings import E5EmbeddingModel, EmbeddingUnavailable
from agent_runtime.llm.embedding_adapter import E5EmbeddingAdapter
from agent_runtime.llm.errors import (
    ModelContextTooLargeError,
)
from pydantic import ValidationError

pytestmark = pytest.mark.integration

from tests.integration.e5_snapshot import provisioned_e5_snapshot


@pytest.fixture(scope="module")
def real_e5_model():
    snapshot_path = provisioned_e5_snapshot()
    if snapshot_path is None or not (snapshot_path / "model.safetensors").is_file():
        pytest.skip("NOT_RUN: provision E5_MODEL_SNAPSHOT offline")
    import torch

    torch.set_num_threads(2)
    return E5EmbeddingModel(snapshot_path)


def test_real_e5_vector_shape_finite_and_normalization(real_e5_model) -> None:
    adapter = E5EmbeddingAdapter(real_e5_model)
    req = ModelEmbeddingRequest(
        request_id=uuid4(),
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        model_id="intfloat/multilingual-e5-small",
        inputs=["Làm sao xin nghỉ phép?"],
        purpose="query",
    )

    res = asyncio.run(adapter.embed(req))

    assert res.dimensions == 384
    assert res.count == 1
    assert len(res.embeddings) == 1
    vector = res.embeddings[0]
    assert len(vector) == 384
    assert all(math.isfinite(val) for val in vector)
    norm = math.sqrt(sum(v * v for v in vector))
    assert pytest.approx(norm, abs=1e-3) == 1.0


def test_real_e5_cancellation_storm_drains_queue_and_retains_thread_permit(real_e5_model):
    import gc
    import threading

    entered = threading.Event()
    release = threading.Event()

    class GatedModel:
        def count_tokens(self, texts, *, purpose):
            return real_e5_model.count_tokens(texts, purpose=purpose)

        def embed_query(self, text):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Owned E5 gate timed out")
            return real_e5_model.embed_query(text)

    errors = []

    async def check():
        asyncio.get_running_loop().set_exception_handler(lambda _, context: errors.append(context))
        adapter = E5EmbeddingAdapter(GatedModel(), max_concurrency=1, max_queue=9)
        req = ModelEmbeddingRequest(
            requestId=uuid4(),
            tenantId=uuid4(),
            workspaceId=uuid4(),
            modelId="intfloat/multilingual-e5-small",
            inputs=["Kiểm tra hủy xử lý"],
            purpose="query",
        )
        tasks = [asyncio.create_task(adapter.embed(req)) for _ in range(10)]
        try:
            assert await asyncio.to_thread(entered.wait, 5)

            async def queued():
                while adapter._pending != 10:
                    await asyncio.sleep(0)

            await asyncio.wait_for(queued(), 5)
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(isinstance(result, asyncio.CancelledError) for result in results)
            assert adapter._pending == 1 and len(adapter._tasks) == 1
            assert adapter._semaphore._value == 0
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.wait_for(adapter.aclose(), 15)
        assert adapter._pending == 0 and not adapter._tasks and adapter._semaphore._value == 1
        result = await adapter.embed(req)
        assert result.dimensions == 384 and result.usage.input_tokens > 0
        await adapter.aclose()
        gc.collect()
        await asyncio.sleep(0)

    asyncio.run(check())
    gc.collect()
    assert errors == [], [context.get("message") for context in errors]


def test_real_e5_semantic_ordering_and_prefix_differentiation(real_e5_model) -> None:
    adapter = E5EmbeddingAdapter(real_e5_model)

    query_req = ModelEmbeddingRequest(
        request_id=uuid4(),
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        model_id="intfloat/multilingual-e5-small",
        inputs=["Làm sao xin nghỉ phép?"],
        purpose="query",
    )
    passages_req = ModelEmbeddingRequest(
        request_id=uuid4(),
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        model_id="intfloat/multilingual-e5-small",
        inputs=[
            "Nhân viên gửi đơn xin nghỉ phép cho quản lý phê duyệt.",
            "Hướng dẫn thay pin bàn phím không dây.",
        ],
        purpose="passage",
    )

    query_res = asyncio.run(adapter.embed(query_req))
    passages_res = asyncio.run(adapter.embed(passages_req))

    q_vec = query_res.embeddings[0]
    p_vec1 = passages_res.embeddings[0]
    p_vec2 = passages_res.embeddings[1]

    sim1 = sum(a * b for a, b in zip(q_vec, p_vec1, strict=True))
    sim2 = sum(a * b for a, b in zip(q_vec, p_vec2, strict=True))

    assert sim1 > sim2, f"Expected relevant passage similarity ({sim1}) > irrelevant ({sim2})"

    # Prefix differentiation
    same_text = "nghỉ phép"
    q_same = asyncio.run(
        adapter.embed(
            ModelEmbeddingRequest(
                request_id=uuid4(),
                tenant_id=uuid4(),
                workspace_id=uuid4(),
                model_id="intfloat/multilingual-e5-small",
                inputs=[same_text],
                purpose="query",
            )
        )
    ).embeddings[0]
    p_same = asyncio.run(
        adapter.embed(
            ModelEmbeddingRequest(
                request_id=uuid4(),
                tenant_id=uuid4(),
                workspace_id=uuid4(),
                model_id="intfloat/multilingual-e5-small",
                inputs=[same_text],
                purpose="passage",
            )
        )
    ).embeddings[0]

    assert q_same != p_same, (
        "Query prefix and passage prefix must produce distinct vector representations"
    )


def test_real_e5_bounded_concurrency(real_e5_model) -> None:
    """Verify multiple concurrent requests are processed with bounded semaphore without blocking event loop."""
    adapter = E5EmbeddingAdapter(real_e5_model, max_concurrency=4)

    async def _make_call(idx: int):
        req = ModelEmbeddingRequest(
            request_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            model_id="intfloat/multilingual-e5-small",
            inputs=[f"Tác vụ số {idx} kiểm tra concurrency."],
            purpose="query",
        )
        return await adapter.embed(req)

    async def _run_concurrent():
        return await asyncio.gather(*[_make_call(i) for i in range(8)])

    results = asyncio.run(_run_concurrent())
    assert len(results) == 8
    for res in results:
        assert res.dimensions == 384
        assert res.count == 1
        assert math.isclose(sum(v * v for v in res.embeddings[0]), 1.0, abs_tol=1e-3)


def test_real_e5_input_limits_and_rejections(real_e5_model) -> None:
    adapter = E5EmbeddingAdapter(real_e5_model)

    # 1. Oversize tokens (>512 tokens)
    long_text = "từ khóa " * 600
    with pytest.raises(ModelContextTooLargeError, match="token limit exceeded"):
        asyncio.run(
            adapter.embed(
                ModelEmbeddingRequest(
                    request_id=uuid4(),
                    tenant_id=uuid4(),
                    workspace_id=uuid4(),
                    model_id="intfloat/multilingual-e5-small",
                    inputs=[long_text],
                    purpose="passage",
                )
            )
        )

    # 2. Batch size overflow (>32 items) rejected at contract / adapter boundary
    with pytest.raises(ValidationError):
        req = ModelEmbeddingRequest(
            request_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            model_id="intfloat/multilingual-e5-small",
            inputs=["văn bản"] * 33,
            purpose="passage",
        )
        asyncio.run(adapter.embed(req))

    # 3. Empty input text rejected
    with pytest.raises(ValidationError, match="non-empty string"):
        req = ModelEmbeddingRequest(
            request_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            model_id="intfloat/multilingual-e5-small",
            inputs=["   "],
            purpose="query",
        )
        asyncio.run(adapter.embed(req))


def test_real_e5_revision_and_integrity_check(tmp_path: Path) -> None:
    # 1. Mismatched revision folder name
    wrong_folder = tmp_path / "wrong-revision-abc"
    wrong_folder.mkdir()
    with pytest.raises(ValueError, match="embedding_snapshot_revision_mismatch"):
        E5EmbeddingModel(wrong_folder)

    # 2. Correct folder name but missing model.safetensors
    pinned_folder = tmp_path / "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    pinned_folder.mkdir()
    (pinned_folder / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(EmbeddingUnavailable, match="embedding_snapshot_not_provisioned"):
        E5EmbeddingModel(pinned_folder)
