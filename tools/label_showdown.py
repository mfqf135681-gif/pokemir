"""Semi-automatic labeling CLI for showdown card crops.

Supports two input modes:

A) Pipeline-harvested dumps (data/showdown_dumps/<hand_id>/seat_X_<L|R>_HHMMSS.png)
   — sibling .json holds CNN's guess + conf; Enter accepts.

B) Manual screenshots (drop PNGs into any dir, e.g. data/showdown_manual/)
   — no .json needed; PNG width > 1.4× height auto-split into L/R halves
   (one screenshot of a seat's card pair → labeled as 2 cards).

Keys per crop:
    Enter   accept CNN's guess (mode A only) — skipped if no guess available
    <text>  type the true card code (e.g. "9s", "Th", "Kc") + Enter
    s       skip this crop (decide later)
    d       this is NOT a card (garbage / avatar / overlay) → noncard pool
    q       quit (saves nothing for current crop)

Output:
    tests/fixtures/showdown/<rank><suit>/<filename>_<side>.png
    tests/fixtures/showdown_noncard/<filename>.png   (for "d")

Idempotency: a sibling `.labeled` marker is written next to each source PNG
once fully processed. Re-runs skip already-labeled files. For auto-split mode,
marker only written after BOTH halves are labeled (or skipped/quit clears it).

Usage:
    python tools/label_showdown.py                            # default: dumps + manual
    python tools/label_showdown.py --dir data/showdown_manual # specific dir
    python tools/label_showdown.py --no-split                 # force single-card mode (no L/R split)
    python tools/label_showdown.py --no-display               # blind label (typed only)
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

# Aspect ratio above which a crop is assumed to contain TWO cards side-by-side
# (wide image with both hole cards visible).  Tuned for WePoker showdown UI
# where each card is ~0.7 aspect ratio (taller than wide), so a 2-card pair is
# ~1.4 aspect ratio.
SPLIT_ASPECT_RATIO = 1.4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DUMPS = PROJECT_ROOT / "data" / "showdown_dumps"
DEFAULT_MANUAL = PROJECT_ROOT / "data" / "showdown_manual"
FIXTURES_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "showdown"
NONCARD_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "showdown_noncard"


def _valid_card(s: str) -> bool:
    s = s.strip()
    return len(s) == 2 and s[0].upper() in RANKS and s[1].lower() in SUITS


def _normalize(s: str) -> str:
    return s.strip()[0].upper() + s.strip()[1].lower()


def _read_img(png_path: Path):
    """Read PNG via cv2; returns BGR ndarray or None."""
    try:
        import cv2
        return cv2.imread(str(png_path))
    except ImportError:
        return None


def _split_pieces(img, no_split: bool) -> list[tuple[str, "object"]]:
    """Return list of (piece_id, image_np) to label.

    Wide images (w / h > SPLIT_ASPECT_RATIO) → [('L', left), ('R', right)]
    Otherwise (or --no-split) → [('whole', img)]
    """
    if img is None:
        return []
    h, w = img.shape[:2]
    if not no_split and h > 0 and w / h > SPLIT_ASPECT_RATIO:
        mid = w // 2
        return [('L', img[:, :mid]), ('R', img[:, mid:])]
    return [('whole', img)]


def _piece_marker(source: Path, piece_id: str) -> Path:
    """Path of the per-piece idempotency marker (separate for L / R / whole).

    Naming: foo.png → foo.L.labeled / foo.R.labeled / foo.whole.labeled.
    Backward compat: if a legacy foo.labeled (no piece suffix) exists, all
    pieces are considered done.
    """
    return source.with_suffix(f".{piece_id}.labeled")


def _is_piece_done(source: Path, piece_id: str) -> bool:
    if source.with_suffix(".labeled").exists():
        return True  # legacy whole-file marker
    return _piece_marker(source, piece_id).exists()


def _iter_unlabeled(roots: list[Path], no_split: bool):
    """Yield (source_png, piece_id, piece_img_np, meta_dict) for unlabeled pieces."""
    for root in roots:
        if not root.exists():
            continue
        for source in sorted(root.rglob("*.png")):
            img = _read_img(source)
            if img is None:
                continue
            json_path = source.with_suffix(".json")
            meta = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else {}
            for piece_id, piece_img in _split_pieces(img, no_split):
                if _is_piece_done(source, piece_id):
                    continue
                yield source, piece_id, piece_img, meta


# Single fixed window name so each piece UPDATES the same window instead of
# spawning a new one (which caused L+R windows to stack visible at once).
_DISPLAY_WIN = "label_showdown"


def _display(_caption_unused: str, img) -> str | None:
    """Show img in the single shared cv2 window (upscaled). Returns window name."""
    try:
        import cv2
    except ImportError:
        return None
    if img is None:
        return None
    h, w = img.shape[:2]
    big = cv2.resize(img, (w * 4, h * 4), interpolation=cv2.INTER_NEAREST)
    cv2.imshow(_DISPLAY_WIN, big)
    cv2.waitKey(1)  # force window paint
    return _DISPLAY_WIN


def _close(win):
    if win is None:
        return
    try:
        import cv2
        cv2.destroyWindow(win)
        cv2.waitKey(1)
    except Exception:
        pass


def _save_card_piece(source: Path, piece_id: str, piece_img, card: str) -> Path:
    """Write piece_img to tests/fixtures/showdown/<card>/<stem>_<piece>.png + mark done."""
    import cv2
    dst_dir = FIXTURES_ROOT / card
    dst_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if piece_id == "whole" else f"_{piece_id}"
    dst = dst_dir / f"{source.stem}{suffix}.png"
    cv2.imwrite(str(dst), piece_img)
    _piece_marker(source, piece_id).write_text(f"card={card}\n", encoding="utf-8")
    return dst


def _save_noncard_piece(source: Path, piece_id: str, piece_img) -> Path:
    import cv2
    NONCARD_ROOT.mkdir(parents=True, exist_ok=True)
    suffix = "" if piece_id == "whole" else f"_{piece_id}"
    dst = NONCARD_ROOT / f"{source.stem}{suffix}.png"
    cv2.imwrite(str(dst), piece_img)
    _piece_marker(source, piece_id).write_text("card=NONCARD\n", encoding="utf-8")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description="Label showdown card crops for CNN retraining.")
    ap.add_argument("--dir", type=Path, default=None,
                    help=f"Source dir (default: both {DEFAULT_DUMPS.relative_to(PROJECT_ROOT)} "
                         f"and {DEFAULT_MANUAL.relative_to(PROJECT_ROOT)})")
    ap.add_argument("--no-split", action="store_true",
                    help=f"Disable aspect-ratio auto-split (default: w/h > {SPLIT_ASPECT_RATIO} → L/R)")
    ap.add_argument("--no-display", action="store_true",
                    help="Skip cv2 window (blind labeling — only metadata shown)")
    args = ap.parse_args()

    if args.dir is not None:
        roots = [args.dir]
    else:
        roots = [DEFAULT_DUMPS, DEFAULT_MANUAL]

    existing = [r for r in roots if r.exists()]
    if not existing:
        print(f"No source dirs found. Tried: {[str(r) for r in roots]}")
        print(f"Drop manual screenshots under {DEFAULT_MANUAL.relative_to(PROJECT_ROOT)}/ "
              f"or run the pipeline to populate {DEFAULT_DUMPS.relative_to(PROJECT_ROOT)}/.")
        return 1

    todo = list(_iter_unlabeled(existing, args.no_split))
    if not todo:
        print(f"Nothing to label in {[str(r.relative_to(PROJECT_ROOT)) for r in existing]} "
              f"(all pieces have .labeled markers).")
        return 0

    print(f"Found {len(todo)} unlabeled pieces across {len(existing)} source dir(s).\n")
    print("Keys: <Enter>=accept CNN guess (if any)  <text>=type true card (e.g. 9s)")
    print("      s=skip  d=noncard/garbage  q=quit\n")

    saved_card = 0
    saved_noncard = 0
    skipped = 0

    for i, (source, piece_id, piece_img, meta) in enumerate(todo, 1):
        pred = meta.get("cnn_prediction")
        guess = f"{pred['rank']}{pred['suit']}" if pred else "—"
        rc = pred.get("rank_conf") if pred else None
        sc = pred.get("suit_conf") if pred else None
        seat = meta.get("seat", "?")

        try:
            rel = source.relative_to(PROJECT_ROOT)
        except ValueError:
            rel = source

        print(f"[{i}/{len(todo)}] {rel} piece={piece_id} seat={seat}")
        if pred:
            print(f"  CNN guess: {guess}  (rank_conf={rc} suit_conf={sc})")

        win = None if args.no_display else _display(f"label — {source.name} [{piece_id}]", piece_img)
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
            dst = _save_noncard_piece(source, piece_id, piece_img)
            saved_noncard += 1
            print(f"  → noncard: {dst.relative_to(PROJECT_ROOT)}\n")
            continue
        if user == "":  # accept CNN guess
            if pred and _valid_card(guess):
                card = _normalize(guess)
                dst = _save_card_piece(source, piece_id, piece_img, card)
                saved_card += 1
                print(f"  → {card}: {dst.relative_to(PROJECT_ROOT)}\n")
            else:
                print("  ! no CNN guess to accept; skipping\n")
                skipped += 1
            continue
        if _valid_card(user):
            card = _normalize(user)
            dst = _save_card_piece(source, piece_id, piece_img, card)
            saved_card += 1
            print(f"  → {card}: {dst.relative_to(PROJECT_ROOT)}\n")
        else:
            print(f"  ! '{user}' not a valid card (e.g. 9s, Th, Kc, As); skipping\n")
            skipped += 1

    print(f"\nDone. saved_card={saved_card} noncard={saved_noncard} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
