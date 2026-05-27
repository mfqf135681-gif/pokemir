"""Action recognition from OCR text extracted from the action area ROI."""

import re
from typing import Optional

from events.models import ActionType


class ActionRecognizer:
    """Parses OCR text from an action ROI into structured action data.

    Handles English + simplified-Chinese WePoker action text:
        "FOLD" / "弃牌"          → ActionType.FOLD
        "CHECK" / "过牌"          → ActionType.CHECK
        "CALL $2.50" / "跟注 100" → ActionType.CALL, amount=2.50/100
        "BET $5.00" / "下注 50"   → ActionType.BET, amount=5.00/50
        "RAISE TO $15.00" / "加注 300" → ActionType.RAISE, amount=15.00/300
        "ALL-IN $42.75" / "全下"  → ActionType.ALL_IN, amount=42.75/None
        "SB" / "小盲"             → ActionType.POST_SB
        "BB" / "大盲"             → ActionType.POST_BB
    """

    AMOUNT_RE = re.compile(r"\$?(\d+\.?\d*)", re.IGNORECASE)

    def parse(self, text: str) -> Optional[dict]:
        """Parse OCR text into {"action_type": ActionType, "amount": float|None}."""
        # NB: .upper() leaves Chinese chars untouched (ASCII-only upcasing).
        text = text.strip().upper()

        if not text:
            return None

        amount = self._extract_amount(text)

        # ALL-IN must be checked before CALL ("CALL" contains "ALL"; same trap).
        # WePoker 实测显示 "All in"(无横杠,首字母大写) — .upper() 转 "ALL IN"
        if ("ALL-IN" in text or "ALLIN" in text or "ALL IN" in text
                or "全下" in text or "全押" in text or "全压" in text):
            return {"action_type": ActionType.ALL_IN, "amount": amount}
        # ALL as a standalone word (but not inside CALL) — 兜底 "ALL XXX" 变体
        if re.search(r"\bALL\b", text) and "CALL" not in text:
            return {"action_type": ActionType.ALL_IN, "amount": amount}
        if "RAISE" in text or "加注" in text:
            return {"action_type": ActionType.RAISE, "amount": amount}
        if "BET" in text or "下注" in text:
            return {"action_type": ActionType.BET, "amount": amount}
        if "CALL" in text or "跟注" in text:
            return {"action_type": ActionType.CALL, "amount": amount}
        if "CHECK" in text or "过牌" in text or "让牌" in text or "看牌" in text:
            return {"action_type": ActionType.CHECK, "amount": None}
        if "FOLD" in text or "弃牌" in text or "盖牌" in text:
            return {"action_type": ActionType.FOLD, "amount": None}
        # Post-blind detection: check SMALL BLIND / 小盲 BEFORE 大盲 (substring of "小大")
        if "SMALL BLIND" in text or "小盲" in text:
            return {"action_type": ActionType.POST_SB, "amount": amount}
        if "BIG BLIND" in text or "大盲" in text:
            return {"action_type": ActionType.POST_BB, "amount": amount}
        # Bare SB / BB after Chinese checks (avoid matching e.g. "BET 5sB")
        if re.search(r"\bSB\b", text):
            return {"action_type": ActionType.POST_SB, "amount": amount}
        if re.search(r"\bBB\b", text):
            return {"action_type": ActionType.POST_BB, "amount": amount}
        if "ANTE" in text or "前注" in text:
            return {"action_type": ActionType.POST_ANTE, "amount": amount}

        return None

    @staticmethod
    def _extract_amount(text: str) -> Optional[float]:
        m = ActionRecognizer.AMOUNT_RE.search(text)
        return float(m.group(1)) if m else None
