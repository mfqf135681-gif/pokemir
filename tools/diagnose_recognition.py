"""Diagnostic: rename copies of fixture PNGs using the recognizer's output.

For each `tests/fixtures/cards/<id>.png` fixture, runs CardRecognizer.recognize_single
and saves a copy into `tests/fixtures/cards/_diagnosis/` named:
    <recognized_rank><recognized_suit>_<seq>.png

…or `NONE_<seq>.png` if recognizer returns None. Lets a human eyeball
mismatches by comparing the filename to the visible card.

The `_diagnosis/` subdir has a `_` prefix so the fixture loader
(tests/test_recognition_fixtures.py) ignores it. Original fixture
files are untouched.

Usage (any OS):
    python tools/diagnose_recognition.py

Output:
    tests/fixtures/cards/_diagnosis/
        Ah_001.png   ← recognizer thinks this is Ah; check by opening
        Ks_002.png
        NONE_003.png ← recognizer returned None
        …
"""

import shutil
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).parent.parent))

from recognition.cards import CardRecognizer

SRC = Path(__file__).parent.parent / "tests" / "fixtures" / "cards"
OUT = SRC / "_diagnosis"


def main() -> int:
    if not SRC.exists():
        print(f"✗ Fixture dir not found: {SRC}", file=sys.stderr)
        return 1

    OUT.mkdir(exist_ok=True)
    # Clear stale diagnostics
    for old in OUT.glob("*.png"):
        old.unlink()

    pngs = sorted(p for p in SRC.glob("*.png") if not p.name.startswith("_"))
    if not pngs:
        print(f"✗ No fixtures found under {SRC}", file=sys.stderr)
        return 1

    rec = CardRecognizer()
    correct = 0
    wrong = 0
    none = 0

    print(f"Running recognizer over {len(pngs)} fixtures...\n")
    for idx, png in enumerate(pngs, 1):
        img = cv2.imread(str(png))
        if img is None:
            print(f"  {idx:03d}  {png.name}  → cv2.imread failed (skip)")
            continue

        result = rec.recognize_single(img)
        if result:
            label = f"{result.get('rank', '?')}{result.get('suit', '?')}"
        else:
            label = "NONE"
            none += 1

        # Compare to ground truth (parsed from original filename: <rank><suit>_<source>_<seq>)
        expected = png.stem.split("_")[0]
        if label == expected:
            correct += 1
            marker = "✓"
        elif label == "NONE":
            marker = "?"
        else:
            wrong += 1
            marker = "✗"

        dest = OUT / f"{label}_{idx:03d}.png"
        shutil.copy(png, dest)
        print(f"  {idx:03d}  {marker}  expected={expected:3s} recognized={label:4s}  → {dest.name}")

    print()
    total = len(pngs)
    print(f"Summary: {correct} correct / {wrong} wrong / {none} unrecognized (out of {total})")
    print(f"Accuracy: {correct / total:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
