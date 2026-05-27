"""Compare training distribution vs pipeline-captured (inference) distribution.

Diagnoses the "trained 100% val but live inference fails" pattern by computing
pixel-level statistics on two image sets:

  A. Training fixtures   — tests/fixtures/showdown/<card>/*.png
                            (these are user's manual screenshots, labeled)
  B. Pipeline dumps      — data/showdown_dumps/<hand_id>/seat_*_<L|R>_*.png
                            (these are pipeline's capture_roi output)

If A and B are statistically similar → distribution gap is NOT the issue.
If they differ significantly → CNN overfit to A's quirks, won't generalize to B.

Metrics computed:
- Mean pixel value per channel (RGB)
- Std of pixel value per channel
- Mean L2 distance between resized-aligned samples (lower = more similar)
- Color histogram chi-square distance (lower = more similar)
- File size distribution (proxy for compression/source)

Usage:
    python tools/diff_showdown_distribution.py
    python tools/diff_showdown_distribution.py --max-per-set 100
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

PROJ = Path(__file__).resolve().parent.parent
SHOWDOWN_FIXTURES = PROJ / "tests" / "fixtures" / "showdown"
SHOWDOWN_DUMPS = PROJ / "data" / "showdown_dumps"

# Standard size we resize all crops to for fair comparison
COMPARE_H, COMPARE_W = 96, 64


def _load_set(root: Path, pattern: str, max_n: int = 200) -> list[tuple[Path, np.ndarray]]:
    """Walk root, load up to max_n PNGs matching pattern, resize to COMPARE_HxCOMPARE_W."""
    out = []
    pngs = sorted(root.rglob(pattern))
    if not pngs:
        return out
    for p in pngs[:max_n]:
        try:
            img = Image.open(p).convert("RGB").resize((COMPARE_W, COMPARE_H), Image.BILINEAR)
            out.append((p, np.asarray(img, dtype=np.float32)))
        except Exception:
            continue
    return out


def _stats(samples: list[np.ndarray]) -> dict:
    """Mean / std per channel + brightness histogram (32 bins)."""
    if not samples:
        return {"n": 0}
    stack = np.stack(samples)  # (N, H, W, 3)
    means = stack.reshape(-1, 3).mean(axis=0)
    stds = stack.reshape(-1, 3).std(axis=0)
    # Aggregate luminance histogram
    lum = (stack[..., 0] * 0.299 + stack[..., 1] * 0.587 + stack[..., 2] * 0.114).flatten()
    hist, _ = np.histogram(lum, bins=32, range=(0, 256))
    hist = hist / hist.sum()
    return {
        "n": len(samples),
        "mean_rgb": means.tolist(),
        "std_rgb": stds.tolist(),
        "lum_hist": hist.tolist(),
    }


def _chi_square(h1: list, h2: list) -> float:
    """Chi-square distance between two normalized histograms."""
    a = np.asarray(h1) + 1e-10
    b = np.asarray(h2) + 1e-10
    return float(np.sum((a - b) ** 2 / (a + b)))


def _per_card_compare(fixture_root: Path, dump_root: Path, max_per_card: int = 50) -> None:
    """For each card class with both fixture + dump samples, compute mean-pixel L2 diff."""
    if not fixture_root.exists():
        return
    print("\n## Per-card distribution overlap")
    print(f"{'card':<6}{'fix_n':>7}{'dump_pred_n':>13}{'lum_chi2':>10}{'rgb_dist':>10}")

    # Pre-load all dump samples with their predicted card from sibling JSON
    dump_by_pred: dict[str, list[np.ndarray]] = {}
    if dump_root.exists():
        for png in sorted(dump_root.rglob("seat_*_*_*.png")):
            json_path = png.with_suffix(".json")
            if not json_path.exists():
                continue
            try:
                meta = json.loads(json_path.read_text(encoding="utf-8"))
                pred = meta.get("cnn_prediction") or {}
                card = (pred.get("rank") or "") + (pred.get("suit") or "")
                if len(card) != 2:
                    continue
                img = Image.open(png).convert("RGB").resize((COMPARE_W, COMPARE_H), Image.BILINEAR)
                dump_by_pred.setdefault(card, []).append(np.asarray(img, dtype=np.float32))
            except Exception:
                continue

    for card_dir in sorted(fixture_root.iterdir()):
        if not card_dir.is_dir() or len(card_dir.name) != 2:
            continue
        card = card_dir.name
        fix_samples = _load_set(card_dir, "*.png", max_per_card)
        fix_imgs = [s[1] for s in fix_samples]
        dump_imgs = dump_by_pred.get(card, [])[:max_per_card]
        if not fix_imgs or not dump_imgs:
            continue
        fs = _stats(fix_imgs)
        ds = _stats(dump_imgs)
        chi2 = _chi_square(fs["lum_hist"], ds["lum_hist"])
        rgb_dist = float(np.linalg.norm(
            np.asarray(fs["mean_rgb"]) - np.asarray(ds["mean_rgb"])
        ))
        print(f"{card:<6}{fs['n']:>7}{ds['n']:>13}{chi2:>10.4f}{rgb_dist:>10.2f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-per-set", type=int, default=200,
                    help="Max samples per group for global stats (default: 200)")
    ap.add_argument("--max-per-card", type=int, default=50,
                    help="Max samples per card-class for per-class comparison (default: 50)")
    args = ap.parse_args()

    # Global comparison: ALL fixture samples vs ALL pipeline-dumped samples
    print("# Showdown Distribution Diff Report")
    print(f"Fixture root:  {SHOWDOWN_FIXTURES}")
    print(f"Dump root:     {SHOWDOWN_DUMPS}")
    print(f"Resize target: {COMPARE_H}x{COMPARE_W} BILINEAR")
    print()

    fix_samples = _load_set(SHOWDOWN_FIXTURES, "*.png", args.max_per_set)
    dump_samples = _load_set(SHOWDOWN_DUMPS, "seat_*_*_*.png", args.max_per_set)

    print(f"Loaded: fixtures={len(fix_samples)}  pipeline_dumps={len(dump_samples)}")
    if not fix_samples:
        print("✗ No fixtures under tests/fixtures/showdown/ — label some first.")
        return 1
    if not dump_samples:
        print("✗ No pipeline dumps under data/showdown_dumps/ matching seat_*_*_*.png pattern.")
        print("  (Manual-named screenshots like 'f001 (1).png' do not count — they're user-cropped,")
        print("   not pipeline-captured. Run pipeline normally to produce seat_X_L/R_*.png dumps.)")
        return 1

    fs = _stats([s[1] for s in fix_samples])
    ds = _stats([s[1] for s in dump_samples])

    print("\n## Global stats")
    print(f"{'metric':<24}{'fixtures':>14}{'dumps':>14}{'gap':>14}")
    for i, ch in enumerate("RGB"):
        gap = ds["mean_rgb"][i] - fs["mean_rgb"][i]
        print(f"{'mean_' + ch:<24}{fs['mean_rgb'][i]:>14.2f}{ds['mean_rgb'][i]:>14.2f}{gap:>+14.2f}")
    for i, ch in enumerate("RGB"):
        gap = ds["std_rgb"][i] - fs["std_rgb"][i]
        print(f"{'std_' + ch:<24}{fs['std_rgb'][i]:>14.2f}{ds['std_rgb'][i]:>14.2f}{gap:>+14.2f}")

    chi2 = _chi_square(fs["lum_hist"], ds["lum_hist"])
    print(f"\nLuminance hist chi-square distance: {chi2:.4f}")
    print(f"  Interpretation:")
    print(f"    < 0.05 → highly similar distributions")
    print(f"    0.05-0.20 → moderate gap (some overfit risk)")
    print(f"    > 0.20 → significant gap (likely root cause of inference failure)")

    rgb_dist = float(np.linalg.norm(
        np.asarray(fs["mean_rgb"]) - np.asarray(ds["mean_rgb"])
    ))
    print(f"Mean RGB euclidean distance: {rgb_dist:.2f}")
    print(f"  Interpretation:")
    print(f"    < 5 → effectively same color distribution")
    print(f"    5-20 → moderate shift (color profile / gamma differ)")
    print(f"    > 20 → significant shift (different capture pipeline)")

    _per_card_compare(SHOWDOWN_FIXTURES, SHOWDOWN_DUMPS, args.max_per_card)

    print("\n## Verdict heuristic")
    if chi2 < 0.05 and rgb_dist < 5:
        print("  ✅ Distributions match — CNN overfit is NOT a distribution-gap issue.")
        print("     Investigate: epoch count, dropout, or model architecture.")
    elif chi2 < 0.20 and rgb_dist < 20:
        print("  ⚠️  Moderate gap — fix likely combines:")
        print("     (a) Train on a mix of fixtures + pipeline-dumped samples")
        print("     (b) Stronger augmentation (RandomErasing, ColorJitter ↑)")
    else:
        print("  🔴 Significant gap — CNN trained on fixtures will NOT generalize to live capture.")
        print("     Required: collect pipeline-dumped samples + retrain.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
