"""
Feast Dual Feature Store (Track 2 Day 19).
Online Store in RAM (simulating Redis < 2ms) for real-time agent profile enrichment.
Offline Store for analytical training.
"""

import time
from typing import Dict, Any, Optional


class FeastDualStore:
    def __init__(self):
        # Online store dictionary: {entity_id: {feature_name: value}}
        self._online_store: Dict[str, Dict[str, Any]] = {}

    def push_online(self, entity_id: str, features: Dict[str, Any]) -> None:
        """Writes partner/customer features to low-latency store."""
        if entity_id not in self._online_store:
            self._online_store[entity_id] = {}
        self._online_store[entity_id].update(features)
        self._online_store[entity_id]["_updated_at"] = time.time()

    def get_online_features(self, entity_id: str) -> Dict[str, Any]:
        """
        Retrieves feature vector in sub-2ms.
        """
        start = time.perf_counter()
        features = self._online_store.get(entity_id, {
            "credit_score": 700,
            "dispute_count": 0,
            "tier": "STANDARD"
        })
        latency_ms = (time.perf_counter() - start) * 1000.0
        return {
            "entity_id": entity_id,
            "features": features,
            "latency_ms": latency_ms
        }

