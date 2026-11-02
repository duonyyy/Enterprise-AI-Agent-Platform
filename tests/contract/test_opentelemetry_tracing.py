"""
Unit and Contract Test for 11-Span OpenTelemetry Distributed Tracing (Track 2 Day 28 IP10).
Verifies:
1. Complete 11-Span execution lifecycle chain.
2. Parent-child span hierarchy linkage.
3. W3C traceparent formatting and propagation.
4. Error capturing and span status tagging.
"""

import pytest
from agent_runtime.observability import AgentTracer, SpanName


def test_w3c_traceparent_propagation():
    incoming_header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    tracer = AgentTracer(traceparent=incoming_header)

    assert tracer.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert tracer.root_parent_span_id == "00f067aa0ba902b7"

    with tracer.start_span(SpanName.HTTP_INGRESS) as span:
        outbound_header = tracer.generate_traceparent()
        assert outbound_header.startswith("00-4bf92f3577b34da6a3ce929d0e0e4736-")
        assert outbound_header.endswith("-01")
        assert span.parent_span_id == "00f067aa0ba902b7"


def test_canonical_11_span_execution_chain():
    tracer = AgentTracer()

    # Emulate the 11-step execution lifecycle
    with tracer.start_span(SpanName.HTTP_INGRESS, {"method": "POST", "path": "/runs"}):
        with tracer.start_span(SpanName.AUTH_TENANT_VERIFY, {"tenant_id": "t-001"}):
            pass

        with tracer.start_span(SpanName.POLICY_PEP_CHECK, {"action": "run:create"}):
            pass

        with tracer.start_span(SpanName.GUARDRAIL_PII_SCRUB, {"scrubbed": True}):
            pass

        with tracer.start_span(SpanName.CONTEXT_SEMANTIC_CACHE, {"cache_hit": False}):
            pass

        with tracer.start_span(SpanName.CONTEXT_ASSEMBLE, {"prompt_tokens": 120}):
            pass

        with tracer.start_span(SpanName.MODEL_GATEWAY_CALL, {"model": "vinai-llama3-70b"}):
            pass

        with tracer.start_span(SpanName.TOOL_DISPATCHER_STEP, {"tool": "knowledge_retriever"}):
            pass

        with tracer.start_span(SpanName.DB_STATE_CHECKPOINT, {"step": 1}):
            pass

        with tracer.start_span(SpanName.EVENT_PUBLISH, {"topic": "agent.run.events"}):
            pass

        with tracer.start_span(SpanName.GUARDRAIL_OUTPUT_FILTER, {"safe": True}):
            pass

    summary = tracer.get_summary()
    assert summary["total_spans"] == 11
    names = [s["name"] for s in summary["spans"]]

    # Verify all 11 canonical spans were recorded
    expected_spans = [
        SpanName.HTTP_INGRESS.value,
        SpanName.AUTH_TENANT_VERIFY.value,
        SpanName.POLICY_PEP_CHECK.value,
        SpanName.GUARDRAIL_PII_SCRUB.value,
        SpanName.CONTEXT_SEMANTIC_CACHE.value,
        SpanName.CONTEXT_ASSEMBLE.value,
        SpanName.MODEL_GATEWAY_CALL.value,
        SpanName.TOOL_DISPATCHER_STEP.value,
        SpanName.DB_STATE_CHECKPOINT.value,
        SpanName.EVENT_PUBLISH.value,
        SpanName.GUARDRAIL_OUTPUT_FILTER.value,
    ]
    assert names == expected_spans

    # Verify parent-child hierarchy (the other 10 spans are children of http.ingress)
    ingress_span = summary["spans"][0]
    for child in summary["spans"][1:]:
        assert child["parent_span_id"] == ingress_span["span_id"]
        assert child["status"] == "OK"
        assert child["duration_ms"] >= 0.0


def test_span_error_recording():
    tracer = AgentTracer()

    with pytest.raises(PermissionError):
        with tracer.start_span(SpanName.POLICY_PEP_CHECK, {"role": "guest"}):
            raise PermissionError("Egress not allowed for guest")

    summary = tracer.get_summary()
    assert summary["total_spans"] == 1
    span = summary["spans"][0]
    assert span["status"] == "ERROR"
    assert "Egress not allowed" in span["error_message"]

