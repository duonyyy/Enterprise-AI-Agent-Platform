"""
Semantic Vector Cache (Track 2 Day 25, FinOps Skill).
Matches semantically identical queries (Cosine >= 90%), returning cached output in < 2ms at $0 cost.
"""

import math
import re
import time
from typing import Dict, List, Optional, Tuple, Any


class SemanticVectorCache:
    def __init__(self, similarity_threshold: float = 0.90, max_entries: int = 10000, ttl_seconds: float = 86400.0):
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.cache: List[Dict[str, Any]] = []

    def _text_to_vector(self, text: str) -> Dict[str, float]:
        """Simple deterministic term-frequency vectorizer for sub-millisecond calculation."""
        tokens = re.findall(r"\w+", text.lower())
        vec: Dict[str, float] = {}
        for token in tokens:
            vec[token] = vec.get(token, 0.0) + 1.0
        
        # Normalize
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            for k in vec:
                vec[k] /= norm
        return vec

    def _cosine_similarity(self, vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        intersection = set(vec1.keys()) & set(vec2.keys())
        return sum(vec1[k] * vec2[k] for k in intersection)

    def lookup(self, query: str) -> Tuple[Optional[str], float, float]:
        """
        Returns (cached_answer, similarity_score, latency_ms).
        """
        start_time = time.perf_counter()
        q_vec = self._text_to_vector(query)

        best_score = 0.0
        best_entry = None

        now = time.time()
        for entry in reversed(self.cache):
            if now - entry["timestamp"] > self.ttl_seconds:
                continue
            score = self._cosine_similarity(q_vec, entry["vector"])
            if score > best_score:
                best_score = score
                best_entry = entry

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        if best_score >= self.similarity_threshold and best_entry:
            return best_entry["response"], best_score, latency_ms

        return None, best_score, latency_ms

    def store(self, query: str, response: str) -> None:
        if len(self.cache) >= self.max_entries:
            self.cache.pop(0)  # FIFO eviction
        self.cache.append({
            "query": query,
            "vector": self._text_to_vector(query),
            "response": response,
            "timestamp": time.time()
        })

