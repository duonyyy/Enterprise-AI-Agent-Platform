"""
Layer 1 Defense: Byte-Level Zero-Width Shield & Unicode Normalizer.
Eliminates invisible prompt injection payloads and homoglyph attacks.
"""

import re
import unicodedata
from typing import Tuple


class ZeroWidthShield:
    # Pattern matching invisible Unicode chars: zero-width spaces, joiners, directional overrides, BOM
    ZERO_WIDTH_PATTERN = re.compile(r"[\u200B-\u200F\uFEFF\u202A-\u202E\u2060-\u206F]")
    
    # Optional CJK glyph detector for erroneous open-source LLM hallucinations in VN text
    CJK_PATTERN = re.compile(r"[\u4e00-\u9fa5\u3040-\u30ff\uac00-\ud7af]")

    def sanitize(self, text: str, strip_cjk: bool = False) -> Tuple[str, int]:
        """
        Sanitizes input string:
        1. Normalizes via Unicode NFKC.
        2. Detects and removes zero-width characters.
        3. Optionally purges stray CJK tokens.
        Returns: (sanitized_text, count_of_removed_invisible_chars)
        """
        if not text:
            return "", 0

        # Step 1: NFKC Normalization
        normalized = unicodedata.normalize("NFKC", text)

        # Step 2: Zero-Width Removal
        matches = self.ZERO_WIDTH_PATTERN.findall(normalized)
        cleaned = self.ZERO_WIDTH_PATTERN.sub("", normalized)

        # Step 3: Optional CJK Purge
        if strip_cjk:
            cleaned = self.CJK_PATTERN.sub("", cleaned)

        return cleaned, len(matches)

