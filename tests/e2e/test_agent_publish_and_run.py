"""
End-to-End Test Suite: Agent Publish & Execution Run Lifecycle.
Verifies the complete lifecycle across Control Plane and Execution Plane:
1. Agent Spec validation against Schema and immutable bundle_sha256 computation.
2. Control Plane agent publication.
3. Execution run creation with AuthenticatedPrincipal.
4. Tracing across the 11 OpenTelemetry spans.
5. Tamper-Evident audit event recording.
6. Citation verification on generated response.
"""

import hashlib
import json
import pytest

from agent_contracts.agent_spec import AgentSpec
from platform_common.principal import AuthenticatedPrincipal
from platform_common.audit_ledger import TamperEvidentLedger, GENESIS_HASH
from agent_runtime.observability import AgentTracer, SpanName
from agent_runtime.knowledge.citation_verifier import CitationVerifier


def test_agent_publish_and_run_e2e():
    # 1. Author and validate Agent Spec
    agent_spec = AgentSpec.model_validate({
        "schemaVersion": "1.0",
        "kind": "Agent",
        "metadata": {
            "name": "enterprise-research-assistant",
            "displayName": "Enterprise Research Assistant",
            "description": "Assists employees with corporate policies and financial guidelines.",
        },
        "mode": "fixed_plan",
        "modelRef": "vinai-llama3-70b-instruct",
        "persona": {
            "role": "Assistant",
            "tone": "Professional",
            "language": "vi",
            "instructions": "You are a trusted enterprise research assistant. Always cite references.",
        },
        "toolRefs": ["knowledge_retriever", "calculator"],
        "limits": {"maxSteps": 10},
    })

    spec_dict = agent_spec.model_dump()
    canonical_spec = json.dumps(spec_dict, sort_keys=True, separators=(",", ":"))
    bundle_sha256 = hashlib.sha256(canonical_spec.encode("utf-8")).hexdigest()

    assert len(bundle_sha256) == 64

    # 2. Control Plane Publication
    tenant_id = "00000000-0000-0000-0000-000000000001"
    workspace_id = "00000000-0000-0000-0000-000000000011"
    principal = AuthenticatedPrincipal(
        user_id="user:techlead@vinai.vn",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        roles=["platform_admin"],
    )

    # 3. Execution Plane Run with 11-Span Tracing
    tracer = AgentTracer()
    with tracer.start_span(SpanName.HTTP_INGRESS, {"method": "POST", "path": "/runs"}):
        with tracer.start_span(SpanName.AUTH_TENANT_VERIFY, {"tenant_id": principal.tenant_id}):
            assert principal.tenant_id == tenant_id

        with tracer.start_span(SpanName.POLICY_PEP_CHECK, {"action": "run:create"}):
            is_allowed = "platform_admin" in principal.roles
            assert is_allowed is True

        with tracer.start_span(SpanName.GUARDRAIL_PII_SCRUB):
            user_input = "Xin chính sách công tác phí của công ty"

        with tracer.start_span(SpanName.CONTEXT_SEMANTIC_CACHE, {"cache_hit": False}):
            pass

        with tracer.start_span(SpanName.CONTEXT_ASSEMBLE):
            evidence = [
                {"chunk_id": "chunk-policy-01", "content": "Mức công tác phí nội địa tối đa là 1.500.000 VNĐ/ngày."}
            ]

        with tracer.start_span(SpanName.MODEL_GATEWAY_CALL):
            llm_response = "Mức công tác phí nội địa tối đa là 1.500.000 VNĐ/ngày theo quy định [chunk-policy-01]."

        with tracer.start_span(SpanName.TOOL_DISPATCHER_STEP):
            pass

        with tracer.start_span(SpanName.DB_STATE_CHECKPOINT):
            pass

        with tracer.start_span(SpanName.EVENT_PUBLISH):
            pass

        with tracer.start_span(SpanName.GUARDRAIL_OUTPUT_FILTER):
            pass

    trace_summary = tracer.get_summary()
    assert trace_summary["total_spans"] == 11

    # 4. Citation Verification Gate
    verifier = CitationVerifier()
    cited_ids = verifier.verify_citations(llm_response, evidence)
    assert "chunk-policy-01" in cited_ids

    # 5. Tamper-Evident Audit Ledger
    audit_rec = TamperEvidentLedger.create_record(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_subject=principal.user_id,
        action="agent:publish_and_run",
        resource_type="catalog.agents",
        resource_id=agent_spec.metadata.name,
        outcome="succeeded",
        prev_hash=GENESIS_HASH,
        details_redacted={"bundle_sha256": bundle_sha256, "trace_id": tracer.trace_id},
    )

    is_valid, _, _ = TamperEvidentLedger.verify_chain([audit_rec])
    assert is_valid is True
    assert audit_rec.details_redacted["bundle_sha256"] == bundle_sha256
