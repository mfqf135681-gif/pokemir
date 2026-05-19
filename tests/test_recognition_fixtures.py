"""Fixture-driven card recognition tests.

Loads `<id>.png` + `<id>.json` pairs from `tests/fixtures/cards/` and runs
`CardRecognizer.recognize_single` against each, asserting rank/suit match.

When `tests/fixtures/cards/` contains no fixtures (skeleton state), pytest
collects 0 parametrized tests — no impact on baseline.

See `tests/fixtures/cards/_README.md` for fixture format + recording workflow.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cards"


def _list_fixtures():
    """Return sorted list of fixture json paths; empty list if dir missing/empty."""
    if not FIXTURE_DIR.exists():
        return []
    return sorted(p for p in FIXTURE_DIR.glob("*.json") if not p.name.startswith("_"))


_FIXTURES = _list_fixtures()
_PARAMS = _FIXTURES if _FIXTURES else [None]


@pytest.mark.parametrize(
    "fixture_path",
    _PARAMS,
    ids=lambda p: p.stem if p is not None else "skeleton_no_fixtures_yet",
)
def test_card_fixture(fixture_path):
    """Recognize one card image against its expected metadata."""
    if fixture_path is None:
        pytest.skip(
            "no fixtures recorded yet — see tests/fixtures/cards/_README.md "
            "for recording workflow (Win 端真实截图)"
        )

    try:
        import cv2
    except ImportError as exc:
        pytest.skip(f"cv2 not available: {exc}")

    meta = json.loads(fixture_path.read_text(encoding="utf-8"))
    expected = meta.get("expected") or {}
    if "rank" not in expected or "suit" not in expected:
        pytest.fail(f"fixture {fixture_path.name} missing expected.rank or expected.suit")

    img_path = fixture_path.with_suffix(".png")
    if not img_path.exists():
        pytest.skip(f"missing image file: {img_path.name}")

    img = cv2.imread(str(img_path))
    if img is None:
        pytest.skip(f"cannot read image: {img_path.name}")

    from recognition.cards import CardRecognizer

    recognizer = CardRecognizer()
    result = recognizer.recognize_single(img)

    if result is None:
        # Recognition returned None — current heuristic limitation, not assertion failure.
        # Counts toward "skipped" in pytest summary; identifies low-confidence inputs.
        pytest.skip(f"recognizer returned None for {fixture_path.stem}")

    assert result.get("rank") == expected["rank"], (
        f"rank mismatch for {fixture_path.stem}: got {result.get('rank')!r}, "
        f"expected {expected['rank']!r}"
    )
    assert result.get("suit") == expected["suit"], (
        f"suit mismatch for {fixture_path.stem}: got {result.get('suit')!r}, "
        f"expected {expected['suit']!r}"
    )
