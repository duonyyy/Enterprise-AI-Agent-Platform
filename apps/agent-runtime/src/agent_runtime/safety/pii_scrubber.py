"""
Layer 2 Defense: Vietnamese PII Scrubber (Decree 13/2023/ND-CP).
Redacts Citizen Identity Cards (CCCD 12 digits), phone numbers, emails, and bank accounts.
"""

import re
from typing import Dict, Tuple


class VietnamesePIIScrubber:
    # 12-digit Vietnamese CCCD pattern
    CCCD_PATTERN = re.compile(r"\b0\d{2}[0-3]\d{2}\d{6}\b|\b\d{12}\b")
    
    # Vietnamese Phone Pattern (+84 or 0 followed by 3, 5, 7, 8, 9 and 8 digits)
    PHONE_PATTERN = re.compile(r"(\+84|0)(3[2-9]|5[6|8|9]|7[0|6-9]|8[1-9]|9[0-9])\d{7}\b")
    
    # Email Pattern
    EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
    
    # Credit / Bank Card (16 to 19 digits with optional spaces or dashes)
    CARD_PATTERN = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4,7}\b")

    def scrub(self, text: str) -> Tuple[str, Dict[str, int]]:
        """
        Redacts sensitive Vietnamese PII and returns redacted text and replacement statistics.
        """
        stats = {"cccd": 0, "phone": 0, "email": 0, "card": 0}
        if not text:
            return "", stats

        def repl_cccd(match):
            stats["cccd"] += 1
            raw = match.group(0)
            return f"[CCCD_REDACTED_***{raw[-3:]}]"

        def repl_phone(match):
            stats["phone"] += 1
            return "[PHONE_REDACTED]"

        def repl_email(match):
            stats["email"] += 1
            return "[EMAIL_REDACTED]"

        def repl_card(match):
            stats["card"] += 1
            return "[BANK_CARD_REDACTED]"

        scrubbed = self.CCCD_PATTERN.sub(repl_cccd, text)
        scrubbed = self.PHONE_PATTERN.sub(repl_phone, scrubbed)
        scrubbed = self.EMAIL_PATTERN.sub(repl_email, scrubbed)
        scrubbed = self.CARD_PATTERN.sub(repl_card, scrubbed)

        return scrubbed, stats

