"""
Transactional Outbox Pattern & SSE Event Dispatcher.
Ensures reliable asynchronous side-effect execution and ordered Server-Sent Events.
"""

import time
import uuid
from typing import Dict, Any, List


class OutboxDispatcher:
    def __init__(self):
        self.outbox_events: List[Dict[str, Any]] = []
        self._seq = 0

    def stage_event(self, event_type: str, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Stages an event atomically with state changes."""
        self._seq += 1
        event = {
            "event_id": f"evt-{uuid.uuid4().hex[:12]}",
            "seq": self._seq,
            "run_id": run_id,
            "event_type": event_type,
            "payload": payload,
            "created_at": time.time(),
            "dispatched": False
        }
        self.outbox_events.append(event)
        return event

    def dispatch_pending(self) -> List[Dict[str, Any]]:
        """Processes undelivered events."""
        dispatched = []
        for evt in self.outbox_events:
            if not evt["dispatched"]:
                evt["dispatched"] = True
                dispatched.append(evt)
        return dispatched

    def format_sse(self, event: Dict[str, Any]) -> str:
        """Formats event as standard SSE stream data."""
        import json
        return f"id: {event['seq']}\nevent: {event['event_type']}\ndata: {json.dumps(event['payload'])}\n\n"

