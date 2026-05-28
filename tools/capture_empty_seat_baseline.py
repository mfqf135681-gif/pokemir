"""T18 (2026-05-28) — 抓空座位 baseline phash 给 pipeline 用。

用法(Win 桌面机):
  1. 打开 WePoker,**确认哪些座位是空的**(没人坐)
  2. cd D:\\project\\pokemir
  3. .venv\\Scripts\\activate
  4. python tools/capture_empty_seat_baseline.py --profile party_poker_8

工具会:
  - 加载 ROI profile
  - 问你"哪些 seat 是空的?"(输入 0,3,7 等)
  - 对每个 seat 抓 fold_area + cards_area phash
  - 存到 rois/empty_seat_baseline_<profile>.json
  - 后续 pipeline _capture_player_ids 加 gate(下次 commit)

依赖 T13 button OCR fix:抓的时刻 button 位置准确。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import cv2
import numpy as np


def _avg_hash_64(img: np.ndarray) -> str:
    """64-bit average hash(跟 pipeline orchestrator 一致)。"""
    if img is None or img.size == 0:
        return "0" * 16
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    small = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
    avg = small.mean()
    bits = (small > avg).flatten()
    val = 0
    for b in bits:
        val = (val << 1) | int(b)
    return f"{val:016x}"


def main():
    parser = argparse.ArgumentParser(description="T18 抓空座位 baseline")
    parser.add_argument("--profile", default="party_poker_8", help="ROI profile (party_poker_8 / party_poker_9)")
    args = parser.parse_args()

    roi_path = PROJECT_ROOT / "rois" / f"{args.profile}.json"
    if not roi_path.exists():
        print(f"❌ ROI profile 不存在: {roi_path}")
        sys.exit(1)

    print(f"📂 加载 ROI profile: {roi_path}")
    with open(roi_path, encoding="utf-8") as f:
        rois = json.load(f)

    seats = rois.get("seats", [])
    num_seats = rois.get("num_seats", len(seats))
    print(f"   profile: {rois.get('name')}, {num_seats} seats")

    # 用户输入空 seat indexes
    raw = input(f"\n哪些 seat 当前是空的?(逗号分隔 0..{num_seats - 1},如 0,3,7;或 q 退出): ").strip()
    if raw.lower() == "q":
        sys.exit(0)
    try:
        empty_idxs = sorted({int(x.strip()) for x in raw.split(",") if x.strip().isdigit()})
    except Exception:
        print("❌ 输入格式错误")
        sys.exit(1)
    if not empty_idxs:
        print("❌ 没有有效 seat index")
        sys.exit(1)
    print(f"\n✅ 标定 {len(empty_idxs)} 个空座: {empty_idxs}")

    # 启动 capturer
    from capture.screen import ScreenCapturer
    capturer = ScreenCapturer()
    window_title = rois.get("window_title", "")
    if window_title:
        if not capturer.find_window_by_title(window_title):
            print(f"⚠️ 找不到窗口 {window_title!r},fallback monitor 1")
            capturer.select_monitor(1)
    else:
        capturer.select_monitor(1)

    # 对每个空 seat 抓 phash
    from capture.roi import _tuple_to_roi  # type: ignore
    baselines: dict[str, dict] = {}
    for idx in empty_idxs:
        seat_data = next((s for s in seats if s.get("seat_index") == idx), None)
        if seat_data is None:
            print(f"   ⚠️ seat_{idx} 不在 profile,跳过")
            continue
        seat_b: dict[str, str] = {}
        for kind in ("fold_area", "cards"):
            roi_tuple = seat_data.get(kind)
            if roi_tuple is None:
                continue
            roi = _tuple_to_roi(roi_tuple, kind)
            img = capturer.capture_roi(roi)
            if img.size == 0:
                print(f"   ⚠️ seat_{idx} {kind} 抓帧空,跳过")
                continue
            h = _avg_hash_64(img)
            seat_b[kind] = h
            print(f"   seat_{idx} {kind}: phash={h}")
        if seat_b:
            baselines[str(idx)] = seat_b

    if not baselines:
        print("\n❌ 没抓到任何 phash,退出")
        sys.exit(1)

    out_path = PROJECT_ROOT / "rois" / f"empty_seat_baseline_{args.profile}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"profile": args.profile, "baselines": baselines}, f, ensure_ascii=False, indent=2)
    print(f"\n💾 写入: {out_path}")
    print(f"   {len(baselines)} 个空座 baseline")
    print(f"\n下一步:我会在后续 commit 改 _capture_player_ids 加 EMPTY_SEAT gate(读这个 json)。")


if __name__ == "__main__":
    main()
