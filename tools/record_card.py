"""Interactive card-fixture recording tool.

Usage (Win PowerShell):
    .\\.venv\\Scripts\\python.exe tools\\record_card.py
    .\\.venv\\Scripts\\python.exe tools\\record_card.py --profile party_poker --source pm

Reads all single-card ROIs from the named profile (hero_card_1 + hero_card_2
+ each individually-configured community card slot), captures them on Enter,
prompts for rank/suit per non-blank ROI, and writes
<rank><suit>_<source>_<NNN>.{png,json} pairs into tests/fixtures/cards/
per the fixture format in _README.md.

Use cases:
  - Hero cards: record at hand-start; works during real play
  - Community cards: record after flop / turn / river is revealed; works even
    while spectating (no real-money cost, no fold-state brightness variance,
    3-5 cards visible per hand). Requires community ROIs configured
    individually (not a single horizontal strip).

Red line compliance:
  R-1: pure screenshot via mss; no input injection / memory access / network.
  R-3: ROIs are small (~50-110 px), physically cannot include nicknames/chat.
  R-9: only uses ScreenCapturer.capture_roi (never capture_raw).
"""

import argparse
import json
import re
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).parent.parent))

from capture.roi import ROIManager
from capture.screen import ScreenCapturer
from config import ROI_CONFIG_DIR

VALID_RANKS = {"2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"}
VALID_SUITS = {"s", "h", "d", "c"}

DEFAULT_OUT_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "cards"
DEFAULT_PROFILE = "party_poker_9"


def next_seq(out_dir: Path, rank: str, suit: str, source: str) -> int:
    """Find the next sequential number for <rank><suit>_<source>_<NNN>.{png,json}."""
    pattern = re.compile(rf"^{re.escape(rank)}{re.escape(suit)}_{re.escape(source)}_(\d+)$")
    max_seq = 0
    if out_dir.exists():
        for p in out_dir.glob("*.json"):
            m = pattern.match(p.stem)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1


def ask_label(prompt: str) -> tuple[str, str] | None:
    """Prompt for 'rank suit' (or 's' to skip). Returns None if skipped."""
    while True:
        raw = input(prompt).strip()
        if raw.lower() in ("s", "skip"):
            return None
        parts = raw.split()
        if len(parts) != 2:
            print("  ✗ 格式错。输入两个值，比如 'A h' / 'T s'，或 's' 跳过")
            continue
        rank, suit = parts[0].upper(), parts[1].lower()
        if rank not in VALID_RANKS:
            print(f"  ✗ rank 必须是 2-9 / T / J / Q / K / A 之一（你输入：{rank!r}）")
            continue
        if suit not in VALID_SUITS:
            print("  ✗ suit 必须是 s(黑桃) / h(红心) / d(方块) / c(梅花)")
            continue
        return rank, suit


def save_fixture(img, out_dir: Path, rank: str, suit: str, source: str, seq: int) -> Path:
    """Write PNG + JSON pair. Returns PNG path."""
    stem = f"{rank}{suit}_{source}_{seq:03d}"
    png_path = out_dir / f"{stem}.png"
    json_path = out_dir / f"{stem}.json"

    bgr = img[..., :3] if img.shape[2] >= 3 else img
    cv2.imwrite(str(png_path), bgr)

    meta = {
        "expected": {"rank": rank, "suit": suit},
        "source": source,
    }
    json_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return png_path


def looks_blank(img) -> bool:
    """Heuristic: is this ROI showing a blank (no card placed) area?"""
    if img.size == 0:
        return True
    gray = img[..., :3].mean(axis=2) if img.ndim == 3 and img.shape[2] >= 3 else img
    return float(gray.std()) < 10.0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record hero-card fixtures from a live poker client.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        help=f"ROI profile name in {ROI_CONFIG_DIR} (default: {DEFAULT_PROFILE})",
    )
    parser.add_argument(
        "--source",
        default="pm",
        help="Short tag used in filename, e.g. 'pm', 'gg' (default: pm)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Where to write fixtures (default: {DEFAULT_OUT_DIR})",
    )
    args = parser.parse_args()

    # Load profile
    profile_path = Path(ROI_CONFIG_DIR) / f"{args.profile}.json"
    if not profile_path.exists():
        available = sorted(p.stem for p in Path(ROI_CONFIG_DIR).glob("*.json"))
        print(f"✗ ROI profile 不存在: {profile_path}", file=sys.stderr)
        print(f"  可用 profile: {available if available else '(无)'}", file=sys.stderr)
        return 1

    roi_manager = ROIManager.from_json(str(profile_path))
    rois = roi_manager.rois

    # Window
    with open(profile_path, encoding="utf-8") as f:
        window_title = json.load(f).get("window_title", "")
    if not window_title:
        print(f"✗ Profile {args.profile} 没填 window_title 字段", file=sys.stderr)
        return 1

    capturer = ScreenCapturer()
    if not capturer.find_window_by_title(window_title):
        print(f"✗ 没找到牌室窗口 {window_title!r}", file=sys.stderr)
        print("  确认 poker 客户端已打开，且窗口标题包含上述字串", file=sys.stderr)
        return 1
    print(f"✓ 找到窗口: {window_title!r}")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Build single-card ROI list: hero_1/2 + each configured community card.
    # Community cards (when configured individually in ROI profile) appear here
    # as separate ROIRegion entries. This lets observer / non-playing sessions
    # record fixtures from publicly-visible community cards too — same renderer
    # as hero, more cards per hand (3-5 per flop/turn/river).
    single_card_rois = [rois.hero_card_1, rois.hero_card_2] + list(rois.community_cards)

    print()
    print(f"Fixture 录制启动")
    print(f"  profile : {args.profile}")
    print(f"  source  : {args.source}")
    print(f"  out dir : {args.out_dir}")
    print(f"  ROIs    : {len(single_card_rois)} 个（hero ×2 + community ×{len(rois.community_cards)}）")
    for roi in single_card_rois:
        print(f"     · {roi.name}: {roi.width}×{roi.height}")
    print()
    print("操作:")
    print("  - 在牌桌摆好你想录的卡 → 按 Enter 截图（脚本逐 ROI 处理）")
    print("  - hero 卡：发牌瞬间录（包括弃牌后暗态都行）")
    print("  - 公共牌：flop/turn/river 出现后整局都可录；观战也行（无 ToS 风险）")
    print("  - 每张非空区会 prompt 你输入 'rank suit'（例: 'A h' / 'T s'）或 's' 跳过")
    print("  - rank: 2-9 / T / J / Q / K / A    suit: s(黑桃) / h(红心) / d(方块) / c(梅花)")
    print("  - 全部录完，输入 'q' 退出")
    print()

    saved = 0
    try:
        while True:
            cmd = input(f"[已录 {saved} 张] 按 Enter 截图，q 退出: ").strip()
            if cmd.lower() == "q":
                break

            for roi in single_card_rois:
                if roi is None or roi.width == 0 or roi.height == 0:
                    continue
                img = capturer.capture_roi(roi)
                if looks_blank(img):
                    print(f"  · {roi.name}: 空白区，跳过")
                    continue
                h, w = img.shape[:2]
                print(f"  · {roi.name}: 截到 {w}×{h}")

                label = ask_label(f"    [{roi.name}] rank suit (或 s 跳过): ")
                if label is None:
                    print(f"    跳过 {roi.name}")
                    continue
                rank, suit = label
                seq = next_seq(args.out_dir, rank, suit, args.source)
                png = save_fixture(img, args.out_dir, rank, suit, args.source, seq)
                print(f"    ✓ 已存 {png.name}")
                saved += 1
    except (KeyboardInterrupt, EOFError):
        print()

    print()
    print(f"本次录制 {saved} 张。")
    if saved > 0:
        print(f"下一步: git add tests/fixtures/cards/*.{{png,json}}")
        print(f"        git commit -m '加 {saved} 张 fixture'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
