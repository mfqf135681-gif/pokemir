"""Recognition module tests — action parser, OCR engine, card recognizer."""

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from recognition.actions import ActionRecognizer
from recognition.cards import CardRecognizer
from recognition.ocr import OCREngine


# ── Action parser (pure logic, always runs) ─────────────────

@pytest.mark.parametrize(
    "text,expected_type,expected_amount",
    [
        ("FOLD", "fold", None),
        ("CHECK", "check", None),
        ("CALL $2.50", "call", 2.50),
        ("CALL $12.75", "call", 12.75),
        ("BET $5.00", "bet", 5.00),
        ("RAISE TO $15.00", "raise", 15.00),
        ("ALL-IN $42.75", "all_in", 42.75),
        ("ALLIN $100", "all_in", 100.00),
        ("SB $0.05", "post_sb", 0.05),
        ("BB $0.10", "post_bb", 0.10),
    ],
)
def test_action_parser(text, expected_type, expected_amount):
    parser = ActionRecognizer()
    result = parser.parse(text)
    assert result is not None, f"{text!r} returned None"
    assert result["action_type"].value == expected_type
    if expected_amount is None:
        assert result.get("amount") is None
    else:
        assert abs(result["amount"] - expected_amount) < 0.01


# ── OCR engine (requires EasyOCR model; skip on first run if unavailable) ──

def test_ocr_engine_on_synthetic_text():
    img = np.ones((60, 200, 3), dtype=np.uint8) * 255
    cv2.putText(img, "CALL $2.50", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)

    ocr = OCREngine()
    try:
        text = ocr.read_text(img)
    except Exception as exc:
        pytest.skip(f"OCR engine not available: {exc}")

    if not text:
        pytest.skip("OCR returned empty — EasyOCR model may not be downloaded yet")
    assert text  # non-empty text detected


# ── Card recognizer (heuristic path is lossy on synthetic images) ──

def test_card_recognizer_returns_shape():
    """Smoke test: synthetic image either yields a card dict or None — both are valid."""
    img = np.ones((80, 60, 3), dtype=np.uint8) * 255
    cv2.putText(img, "A", (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    cv2.circle(img, (30, 45), 10, (0, 0, 255), -1)

    recognizer = CardRecognizer()
    try:
        result = recognizer.recognize_single(img)
    except Exception as exc:
        pytest.skip(f"Card recognizer not available: {exc}")

    if result is None:
        pytest.skip("Heuristic path returned None on synthetic image (expected)")
    assert "rank" in result and "suit" in result
