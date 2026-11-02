"""
Strict Citation Verifier (Anti-Hallucination Gate).
Ensures that all source citations in the LLM output map to valid chunks in the evidence set.
"""

import re
from typing import Set, List, Dict, Any


class InvalidCitationError(Exception):
    """Raised when the LLM hallucinates an unverified citation."""
    pass


class CitationVerifier:
    CITATION_REGEX = re.compile(r"\[([a-zA-Z0-9_\-]+)\]")

    def verify_citations(self, output_text: str, evidence_set: List[Dict[str, Any]]) -> Set[str]:
        """
        Extracts cited IDs and verifies them against the evidence set.
        Raises InvalidCitationError if any cited chunk was fabricated.
        """
        valid_chunk_ids = {item.get("chunk_id") or item.get("doc_id") for item in evidence_set}
        cited_ids = set(self.CITATION_REGEX.findall(output_text))

        # Filter out common markdown or numerical brackets that are not chunk IDs
        candidate_ids = {cid for cid in cited_ids if cid in valid_chunk_ids or len(cid) > 4}

        unverified = candidate_ids - valid_chunk_ids
        if unverified:
            raise InvalidCitationError(
                f"Agent output references unverified citation(s): {unverified}. "
                f"Evidence set only contains: {valid_chunk_ids}"
            )

        return candidate_ids

