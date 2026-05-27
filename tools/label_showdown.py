"""Semi-automatic labeling CLI for showdown card crops harvested by the pipeline.

Walks data/showdown_dumps/, displays each unlabeled crop in a cv2 window,
shows the CNN's guess + confidence, and accepts one keystroke:

    Enter   accept CNN's guess as truth
    <text>  type the true card code (e.g. "9s", "Th", "Kc") + Enter
    s       skip this crop (decide later)
    d       this is NOT a card (garbage / avatar / overlay) → noncard pool
    q       quit

Labeled crops are copied to:
    tests/fixtures/showdown/<rank><suit>/<filename>.png
    tests/fixtures/showdown_noncard/<filename>.png   (for "d")

Already-labeled crops are detected by a sibling marker file (.labeled).
Re-runs are idempotent — just resumes from the first un-labeled crop.

Usage:
    python tools/label_showdown.py                          # default dir
    python tools/label_showdown.py --dir data/other_dumps
    python tools/label_showdown.py --no-display             # blind-label mode (typed only)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Match the CNN training schema (tools/train_card_cnn.py).
RANKS = set("23456789TJQKA")
SUITS = set("shdc")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DUMPS = PROJECT_ROOT / "data" / "showdown_dumps"
FIXTURES_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "showdown"
NONCARD_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "showdown_noncard"


def _valid_card(s: str) -> bool:
    s = s.strip()
    return len(s) == 2 and s[0].upper() in RANKS and s[1].lower() in SUITS


def _normalize(s: str) -> str:
    return s.strip()[0].upper() + s.strip()[1].lower()


def _iter_unlabeled(root: Path):
    """Yield (png_path, meta_dict) for every crop without a sibling .labeled marker."""
    for png in sorted(root.rglob("*.png")):
        marker = png.with_suffix(".labeled")
        if marker.exists():
            continue
        json_path = png.with_suffix(".json")
        meta = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else {}
        yield png, meta


def _display(png_path: Path) -> object | None:
    """Open the PNG in a cv2 window. Returns the window name to destroy, or None."""
    try:
        import cv2
    except ImportError:
        return None
    img = cv2.imread(str(png_path))
    if img is None:
        return None
    win = f"label_showdown — {png_path.name}"
    # Upscale 4x for human-friendly viewing (cards are small ~40x60).
    h, w = img.shape[:2]
    big = cv2.resize(img, (w * 4, h * 4), interpolation=cv2.INTER_NEAREST)
    cv2.imshow(win, big)
    cv2.waitKey(1)  # force window paint
    return win


def _close(win):
    if win is None:
        return
    try:
        import cv2
        cv2.destroyWindow(win)
        cv2.waitKey(1)
    except Exception:
        pass


def _save_card(png_path: Path, card: str) -> Path:
    """Copy PNG into tests/fixtures/showdown/<card>/ and mark labeled."""
    dst_dir = FIXTURES_ROOT / card
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / png_path.name
    shutil.copy2(png_path, dst)
    png_path.with_suffix(".labeled").write_text(f"card={card}\n", encoding="utf-8")
    return dst


def _save_noncard(png_path: Path) -> Path:
    NONCARD_ROOT.mkdir(parents=True, exist_ok=True)
    dst = NONCARD_ROOT / png_path.name
    shutil.copy2(png_path, dst)
    png_path.with_suffix(".labeled").write_text("card=NONCARD\n", encoding="utf-8")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description="Label showdown card crops for CNN retraining.")
    ap.add_argument("--dir", type=Path, default=DEFAULT_DUMPS,
                    help=f"Dumps root (default: {DEFAULT_DUMPS.relative_to(PROJECT_ROOT)})")
    ap.add_argument("--no-display", action="store_true",
                    help="Skip cv2 window (blind labeling — only metadata shown)")
    args = ap.parse_args()

    if not args.dir.exists():
        print(f"No dumps dir at {args.dir} — run pipeline first to harvest crops.")
        return 1

    todo = list(_iter_unlabeled(args.dir))
    if not todo:
        print(f"Nothing to label in {args.dir} (all crops have .labeled markers).")
        return 0

    print(f"Found {len(todo)} unlabeled crops in {args.dir}\n")
    print("Keys: <Enter>=accept CNN guess  <text>=type true card (e.g. 9s)")
    print("      s=skip  d=noncard/garbage  q=quit\n")

    saved_card = 0
    saved_noncard = 0
    skipped = 0

    for i, (png, meta) in enumerate(todo, 1):
        pred = meta.get("cnn_prediction")
        guess = f"{pred['rank']}{pred['suit']}" if pred else "—"
        rc = pred.get("rank_conf") if pred else None
        sc = pred.get("suit_conf") if pred else None
        seat = meta.get("seat", "?")

        print(f"[{i}/{len(todo)}] seat_{seat} side={meta.get('side','?')} hash_diff={meta.get('avatar_hamming','?')}")
        print(f"  PNG: {png.relative_to(args.dir)}")
        print(f"  CNN guess: {guess}  (rank_conf={rc} suit_conf={sc})")

        win = None if args.no_display else _display(png)
        try:
            user = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            _close(win)
            print("\nInterrupted.")
            break
        _close(win)

        if user.lower() == "q":
            break
        if user.lower() == "s":
            skipped += 1
            continue
        if user.lower() == "d":
            dst = _save_noncard(png)
            saved_noncard += 1
            print(f"  → noncard: {dst.relative_to(PROJECT_ROOT)}\n")
            continue
        if user == "":  # accept CNN guess
            if pred and _valid_card(guess):
                card = _normalize(guess)
                dst = _save_card(png, card)
                saved_card += 1
                print(f"  → {card}: {dst.relative_to(PROJECT_ROOT)}\n")
            else:
                print("  ! no CNN guess to accept; skipping\n")
                skipped += 1
            continue
        if _valid_card(user):
            card = _normalize(user)
            dst = _save_card(png, card)
            saved_card += 1
            print(f"  → {card}: {dst.relative_to(PROJECT_ROOT)}\n")
        else:
            print(f"  ! '{user}' not a valid card (e.g. 9s, Th, Kc, As); skipping\n")
            skipped += 1

    print(f"\nDone. saved_card={saved_card} noncard={saved_noncard} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
