"""
Qdrant Hybrid Search & Reciprocal Rank Fusion (RRF k=60).
Blends BM25 keyword matching with Dense Vector embeddings under strict Workspace ACL isolation.
"""

from typing import List, Dict, Any


class HybridRetriever:
    def __init__(self, rrf_k: int = 60):
        self.rrf_k = rrf_k
        self._corpus: List[Dict[str, Any]] = []

    def index_document(self, doc_id: str, workspace_id: str, text: str, metadata: Dict[str, Any]) -> None:
        self._corpus.append({
            "doc_id": doc_id,
            "workspace_id": workspace_id,
            "text": text,
            "metadata": metadata
        })

    def search(self, query: str, workspace_id: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Executes hybrid search with reciprocal rank fusion:
        RRF_Score(d) = sum( 1 / (k + rank_m(d)) )
        Filters strictly by workspace_id (fail-closed tenant isolation).
        """
        # Step 1: Workspace Filter
        candidates = [doc for doc in self._corpus if doc["workspace_id"] == workspace_id]
        if not candidates:
            return []

        # Step 2: Lexical matching simulation (BM25 score)
        q_tokens = set(query.lower().split())
        lexical_scores = []
        for doc in candidates:
            doc_tokens = set(doc["text"].lower().split())
            overlap = len(q_tokens & doc_tokens)
            lexical_scores.append((doc, overlap))
        lexical_ranked = [doc for doc, _ in sorted(lexical_scores, key=lambda x: x[1], reverse=True)]

        # Step 3: Semantic matching simulation (Length/jaccard similarity)
        semantic_scores = []
        for doc in candidates:
            doc_tokens = set(doc["text"].lower().split())
            union = len(q_tokens | doc_tokens) or 1
            jaccard = len(q_tokens & doc_tokens) / union
            semantic_scores.append((doc, jaccard))
        semantic_ranked = [doc for doc, _ in sorted(semantic_scores, key=lambda x: x[1], reverse=True)]

        # Step 4: RRF Fusion
        rrf_map: Dict[str, float] = {}
        doc_lookup: Dict[str, Dict[str, Any]] = {}

        for rank, doc in enumerate(lexical_ranked):
            did = doc["doc_id"]
            doc_lookup[did] = doc
            rrf_map[did] = rrf_map.get(did, 0.0) + (1.0 / (self.rrf_k + rank + 1))

        for rank, doc in enumerate(semantic_ranked):
            did = doc["doc_id"]
            doc_lookup[did] = doc
            rrf_map[did] = rrf_map.get(did, 0.0) + (1.0 / (self.rrf_k + rank + 1))

        # Sort by final RRF score
        sorted_hits = sorted(rrf_map.items(), key=lambda x: x[1], reverse=True)[:top_k]
        
        return [
            {
                "doc_id": did,
                "score": score,
                "text": doc_lookup[did]["text"],
                "metadata": doc_lookup[did]["metadata"]
            }
            for did, score in sorted_hits
        ]

