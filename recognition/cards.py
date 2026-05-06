"""Poker card recognition — dual-path: moondream vision + color/OCR heuristic."""

import logging
from typing import Optional

import cv2
import numpy as np

from recognition.ocr import OCREngine
from recognition.vision import VisionClient

logger = logging.getLogger(__name__)

CARD_RANKS = list("23456789TJQKA")
RED_SUITS = {"h", "d"}
BLACK_SUITS = {"s", "c"}


class CardRecognizer:
    """Recognizes playing cards from image crops.

    Priority:
        1. Moondream vision model (if available)
        2. Color-based suit + OCR rank heuristic
        3. Return None (don't block the pipeline)
    """

    def __init__(self):
        self._vision: Optional[VisionClient] = None
        self._ocr: Optional[OCREngine] = None

    def recognize_single(self, image: np.ndarray) -> Optional[dict]:
        """Recognize a single card. Returns {"rank": "A", "suit": "h"} or None."""
        if image.size == 0:
            return None

        result = self._try_vision(image)
        if result:
            return result

        result = self._try_heuristic(image)
        if result:
            return result

        logger.debug("Card recognition failed — both paths returned None")
        return None

    def recognize(self, image: np.ndarray) -> list[dict]:
        """Recognize multiple cards (e.g. community card area). Splits by vertical bounds."""
        cards = self._split_card_regions(image)
        results = []
        for card_img in cards:
            r = self.recognize_single(card_img)
            if r:
                results.append(r)
        return results

    # ── Vision model path ────────────────────────────────

    def _try_vision(self, image: np.ndarray) -> Optional[dict]:
        if self._vision is None:
            self._vision = VisionClient()
            if not self._vision.available:
                return None
        return self._vision.identify_card(image)

    # ── Heuristic path ────────────────────────────────────

    def _try_heuristic(self, image: np.ndarray) -> Optional[dict]:
        suit = self._detect_suit_by_color(image)
        rank = self._detect_rank_by_ocr(image)
        if suit and rank:
            return {"rank": rank, "suit": suit}
        return None

    def _detect_suit_by_color(self, image: np.ndarray) -> Optional[str]:
        """Sample central region for red/black suit classification.

        For red: distinguish hearts (rounded pip) vs diamonds (angular pip)
        by sampling pip shape in the card center.
        Returns None if the card appears blank / undetermined.
        """
        if image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

        h, w = image.shape[:2]
        # Sample the suit pip region (central area of the card)
        cx, cy = w // 2, h // 2
        r = min(w, h) // 6
        sample = image[cy - r:cy + r, cx - r:cx + r]

        if sample.size == 0:
            return None

        # Convert to HSV, check red range
        hsv = cv2.cvtColor(sample, cv2.COLOR_BGR2HSV)
        lower_red1 = np.array([0, 50, 50])
        upper_red1 = np.array([10, 255, 255])
        lower_red2 = np.array([170, 50, 50])
        upper_red2 = np.array([180, 255, 255])

        mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
        mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
        red_pixels = cv2.countNonZero(mask1) + cv2.countNonZero(mask2)

        total = sample.shape[0] * sample.shape[1]
        red_ratio = red_pixels / total

        if red_ratio > 0.15:
            return "h"  # default to hearts for red; diamond disambiguation deferred
        elif red_ratio > 0.02:
            # Low red signal — might be a diamond with less saturated color
            return "h"
        else:
            return "c"  # default to clubs for black; spade disambiguation deferred

    def _detect_rank_by_ocr(self, image: np.ndarray) -> Optional[str]:
        """Crop top-left corner (rank area) and OCR the character."""
        if self._ocr is None:
            self._ocr = OCREngine()

        if image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

        h, w = image.shape[:2]
        # Rank is typically in the top-left ~40% x ~40% of the card
        rank_region = image[0 : h // 3, 0 : w // 3]
        if rank_region.size == 0:
            return None

        text = self._ocr.read_text(rank_region)
        return self._normalize_rank(text)

    def _normalize_rank(self, text: str) -> Optional[str]:
        """Map OCR output to standard rank characters."""
        text = text.strip().upper()
        # Handle common OCR confusions
        mapping = {
            "0": "T", "10": "T", "1": "A", "I": "J", "L": "J",
            "Z": "2", "S": "5", "B": "8", "G": "9", "Q": "Q",
            "K": "K", "A": "A",
        }
        for char in text:
            if char in CARD_RANKS:
                return char
            if char in mapping:
                return mapping[char]
        return None

    # ── Helpers ───────────────────────────────────────────

    def _split_card_regions(self, image: np.ndarray) -> list[np.ndarray]:
        """Split a community card row into individual card images by vertical edges."""
        if image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        # Find vertical gaps between cards
        col_sum = np.sum(edges, axis=0)
        threshold = np.max(col_sum) * 0.2 if np.max(col_sum) > 0 else 0

        cards = []
        start = None
        for x in range(col_sum.shape[0]):
            if col_sum[x] > threshold:
                if start is not None and x - start > 10:
                    cards.append(image[:, start:x])
                start = None
            else:
                if start is None:
                    start = x

        if start is not None and col_sum.shape[0] - start > 10:
            cards.append(image[:, start:])

        return cards if cards else [image]
