r"""tools/capture_roi_reference.py — 通用 ROI 参照采集(杠杆 B 参照差分的地基)。

借壳重做自孤儿 `capture_empty_seat_baseline.py`(复用其 capturer + `_avg_hash_64` 壳),
换上 B 需要的模型:
  - 采【全部 B 相关 ROI】(timer/fold_text/fold_area/stack/amount,可 --rois 改),不止 fold_area/cards;
  - 带【状态标签】(--state empty/fold/allin/timer/…)→ 同一工具既建空基线、也采状态参照;
  - 每个 ROI 存【多描述子 + crop】(phash + 灰度均值 + 墨占比 + base64 PNG)→ 描述子选择【不锁死】,
    gate 时再用实验定 phash/mean-diff/ink-frac 哪个稳(座位变暗/高亮 → 描述子鲁棒性待验);
  - 多次运行【累积合并】进同一 JSON(先 --state empty 整桌、后在真桌 --state fold 零散补)。

输出:rois/roi_reference_<profile>.json
  {profile, resolution, references: {state: {seat: {roi_kind: {phash, mean, ink_frac, shape, crop_png_b64}}}}}

⚠️ Win-only(cv2 + mss + 真实屏幕)。Linux 仅验语法。
⚠️ 必须在 profile 分辨率(1454×1287)下、对【对应状态】的真实桌面采(空基线=空桌;fold 参照=某座正显弃牌)。

用法(Win,已激活 .venv):
  # 空桌基线(整桌所有座、所有 B-ROI):
  python tools\capture_roi_reference.py --profile party_poker_8 --state empty
  # 某座的弃牌状态参照(屏上 seat3 正显"弃牌"):
  python tools\capture_roi_reference.py --profile party_poker_8 --state fold --seats 3
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import cv2
import numpy as np

from pipeline.orchestrator import _avg_hash_64  # 复用,phash 格式与管线一致
from capture.roi import _tuple_to_roi

# 默认采集的 B 相关 ROI(状态判定 + 数字)。--rois 可覆盖。
DEFAULT_ROIS = ["timer", "fold_text", "fold_area", "stack", "amount"]
INK_TH = 150  # 墨占比阈值,与 digit 配方一致


def _to_gray(img):
    if img.ndim == 3 and img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _descriptors(img):
    """一个 crop → 多描述子(都留,gate 时实验选)。"""
    gray = _to_gray(img)
    mean = float(gray.mean())
    ink_frac = float((gray > INK_TH).mean())
    ok, buf = cv2.imencode(".png", img)
    crop_b64 = base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""
    return {
        "phash": _avg_hash_64(img),
        "mean": round(mean, 2),
        "ink_frac": round(ink_frac, 4),
        "shape": [int(gray.shape[0]), int(gray.shape[1])],
        "crop_png_b64": crop_b64,
    }


def main():
    ap = argparse.ArgumentParser(description="通用 ROI 参照采集(杠杆 B 地基)")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--state", required=True,
                    help="状态标签:empty / fold / allin / timer / …(决定存进 references 的哪个 state)")
    ap.add_argument("--seats", default="",
                    help="只采这些座(逗号,如 '3' 或 '0,3,7');空=profile 所有座")
    ap.add_argument("--rois", default=",".join(DEFAULT_ROIS),
                    help=f"采哪些 ROI 键(逗号);默认 {DEFAULT_ROIS}")
    ap.add_argument("--out", default="",
                    help="输出 JSON;空=rois/roi_reference_<profile>.json(多次运行累积合并)")
    args = ap.parse_args()

    prof_path = _ROOT / "rois" / f"{args.profile}.json"
    if not prof_path.is_file():
        print(f"❌ profile 不存在: {prof_path}"); sys.exit(1)
    prof = json.loads(prof_path.read_text(encoding="utf-8"))
    seats = prof.get("seats", [])
    res = prof.get("resolution")
    roi_kinds = [k.strip() for k in args.rois.split(",") if k.strip()]
    want_seats = ({int(x) for x in args.seats.split(",") if x.strip().isdigit()}
                  or {s["seat_index"] for s in seats})

    # capturer(复用孤儿的窗口查找/回退)
    from capture.screen import ScreenCapturer
    capturer = ScreenCapturer()
    wt = prof.get("window_title", "")
    if wt and capturer.find_window_by_title(wt):
        print(f"📐 tracking window {wt!r}")
    else:
        if wt:
            print(f"⚠️ 找不到窗口 {wt!r} → fallback monitor 1")
        capturer.select_monitor(1)

    # 分辨率自检(错位=参照全废)
    full = capturer.capture()
    if full is not None and full.size and res:
        fh, fw = full.shape[:2]
        if (fw, fh) != (int(res[0]), int(res[1])):
            print(f"⚠️ 截屏 {fw}x{fh} ≠ profile 分辨率 {res[0]}x{res[1]} → ROI 坐标会错位!"
                  f"参照内容不可用。请在 {res[0]}x{res[1]} 下采。")

    # 采集
    captured = {}  # seat -> {roi_kind: descriptors}
    for s in seats:
        sidx = s["seat_index"]
        if sidx not in want_seats:
            continue
        seat_refs = {}
        for kind in roi_kinds:
            roi_tuple = s.get(kind)
            if not (isinstance(roi_tuple, list) and len(roi_tuple) == 4 and roi_tuple[2] > 0):
                continue
            img = capturer.capture_roi(_tuple_to_roi(roi_tuple, kind))
            if img is None or img.size == 0:
                print(f"   ⚠️ seat{sidx} {kind} 抓帧空,跳过"); continue
            d = _descriptors(img)
            seat_refs[kind] = d
            print(f"   seat{sidx:>1} {kind:<10} phash={d['phash'][:16]}… mean={d['mean']:.0f} ink={d['ink_frac']:.3f} {d['shape']}")
        if seat_refs:
            captured[str(sidx)] = seat_refs

    if not captured:
        print("❌ 没采到任何 ROI(座/键不匹配或抓帧空)"); sys.exit(1)

    # 累积合并进输出文件
    out_path = Path(args.out) if args.out else _ROOT / "rois" / f"roi_reference_{args.profile}.json"
    if out_path.is_file():
        doc = json.loads(out_path.read_text(encoding="utf-8"))
    else:
        doc = {"profile": args.profile, "resolution": res, "references": {}}
    refs = doc.setdefault("references", {})
    state_block = refs.setdefault(args.state, {})
    for sidx, seat_refs in captured.items():
        state_block.setdefault(sidx, {}).update(seat_refs)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    n_roi = sum(len(v) for v in captured.values())
    print(f"\n💾 写入 {out_path}")
    print(f"   state='{args.state}':{len(captured)} 座 / {n_roi} 个 ROI 参照(已合并)")
    print(f"   现有 states: {sorted(refs.keys())}")
    print("下一步:① --state empty 整桌空基线 → 喂 B1 跳读闸门;② 真桌零散补 --state fold/allin/timer → 喂 B2 路由器。")


if __name__ == "__main__":
    main()
