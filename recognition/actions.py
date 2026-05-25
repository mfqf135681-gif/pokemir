"""Action recognition from OCR text extracted from the action area ROI."""

import re
from typing import Optional

from events.models import ActionType


class ActionRecognizer:
    """Parses OCR text from an action ROI into structured action data.

    Handles various formats:
        "FOLD"              → ActionType.FOLD
        "CHECK"             → ActionType.CHECK
        "CALL $2.50"        → ActionType.CALL, amount=2.50
        "BET $5.00"         → ActionType.BET, amount=5.00
        "RAISE TO $15.00"   → ActionType.RAISE, amount=15.00
        "ALL-IN $42.75"     → ActionType.ALL_IN, amount=42.75
    """

    AMOUNT_RE = re.compile(r"\$?(\d+\.?\d*)", re.IGNORECASE)

    def parse(self, text: str) -> Optional[dict]:
        """Parse OCR text into {"action_type": ActionType, "amount": float|None}."""
        text = text.strip().upper()

        if not text:
            return None

        amount = self._extract_amount(text)

        # ALL-IN must be checked before CALL ("CALL" contains "ALL")
        if "ALL-IN" in text or "ALLIN" in text:
            return {"action_type": ActionType.ALL_IN, "amount": amount}
        # ALL as a standalone word (but not inside CALL)
        if re.search(r"\bALL\b", text) and "CALL" not in text:
            return {"action_type": ActionType.ALL_IN, "amount": amount}
        if "RAISE" in text:
            return {"action_type": ActionType.RAISE, "amount": amount}
        if "BET" in text:
            return {"action_type": ActionType.BET, "amount": amount}
        if "CALL" in text:
            return {"action_type": ActionType.CALL, "amount": amount}
        if "CHECK" in text:
            return {"action_type": ActionType.CHECK, "amount": None}
        if "FOLD" in text:
            return {"action_type": ActionType.FOLD, "amount": None}
        if "SB" in text or "SMALL BLIND" in text:
            return {"action_type": ActionType.POST_SB, "amount": amount}
        if "BB" in text or "BIG BLIND" in text:
            return {"action_type": ActionType.POST_BB, "amount": amount}
        if "ANTE" in text:
            return {"action_type": ActionType.POST_ANTE, "amount": amount}

        return None

    @staticmethod
    def _extract_amount(text: str) -> Optional[float]:
        m = ActionRecognizer.AMOUNT_RE.search(text)
        return float(m.group(1)) if m else None
