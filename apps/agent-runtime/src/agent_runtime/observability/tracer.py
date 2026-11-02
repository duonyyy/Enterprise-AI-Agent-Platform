"""
11-Span OpenTelemetry Distributed Tracing Engine (Track 2 Day 28 IP10).
Implements W3C traceparent context propagation across the 11 canonical spans
of the Agent execution lifecycle.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import enum
import os
import re
import secrets
import time
from typing import Any, Dict, Generator, List, Optional


class SpanName(str, enum.Enum):
    HTTP_INGRESS = "http.ingress"
    AUTH_TENANT_VERIFY = "auth.tenant_verify"
    POLICY_PEP_CHECK = "policy.pep_check"
    GUARDRAIL_PII_SCRUB = "guardrail.pii_scrub"
    CONTEXT_SEMANTIC_CACHE = "context.semantic_cache"
    CONTEXT_ASSEMBLE = "context.assemble"
    MODEL_GATEWAY_CALL = "model.gateway_call"
    TOOL_DISPATCHER_STEP = "tool.dispatcher_step"
    DB_STATE_CHECKPOINT = "db.state_checkpoint"
    EVENT_PUBLISH = "event.publish"
    GUARDRAIL_OUTPUT_FILTER = "guardrail.output_filter"


@dataclass
class SpanRecord:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    status: str = "OK"  # 'OK' | 'ERROR'
    attributes: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None

    def finish(self, status: str = "OK", error_message: Optional[str] = None):
        self.end_time = time.perf_counter()
        self.duration_ms = (self.end_time - self.start_time) * 1000.0
        self.status = status
        if error_message:
            self.error_message = error_message


class AgentTracer:
    """
    Manages W3C traceparent headers and traces the 11 core agent spans.
    """

    TRACEPARENT_REGEX = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")

    def __init__(self, traceparent: Optional[str] = None):
        self.spans: List[SpanRecord] = []
        self._span_stack: List[SpanRecord] = []

        if traceparent and self.TRACEPARENT_REGEX.match(traceparent):
            m = self.TRACEPARENT_REGEX.match(traceparent)
            self.trace_id = m.group(1)
            self.root_parent_span_id = m.group(2)
        else:
            self.trace_id = secrets.token_hex(16)
            self.root_parent_span_id = None

    def generate_traceparent(self, current_span_id: Optional[str] = None) -> str:
        sid = current_span_id or (self._span_stack[-1].span_id if self._span_stack else secrets.token_hex(8))
        return f"00-{self.trace_id}-{sid}-01"

    @contextmanager
    def start_span(
        self,
        name: SpanName | str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Generator[SpanRecord, None, None]:
        span_id = secrets.token_hex(8)
        parent_id = self._span_stack[-1].span_id if self._span_stack else self.root_parent_span_id
        span_name = name.value if isinstance(name, SpanName) else str(name)

        span = SpanRecord(
            name=span_name,
            trace_id=self.trace_id,
            span_id=span_id,
            parent_span_id=parent_id,
            start_time=time.perf_counter(),
            attributes=dict(attributes or {}),
        )

        self._span_stack.append(span)
        self.spans.append(span)

        try:
            yield span
            if span.end_time is None:
                span.finish(status="OK")
        except Exception as e:
            span.finish(status="ERROR", error_message=str(e))
            raise e
        finally:
            self._span_stack.pop()

    def get_summary(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "total_spans": len(self.spans),
            "spans": [
                {
                    "name": s.name,
                    "span_id": s.span_id,
                    "parent_span_id": s.parent_span_id,
                    "duration_ms": round(s.duration_ms or 0.0, 3),
                    "status": s.status,
                    "attributes": s.attributes,
                    "error_message": s.error_message,
                }
                for s in self.spans
            ],
        }

