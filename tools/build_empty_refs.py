"""tools/build_empty_refs.py — 采空桌参考(占用 + +xx 基线 + 摊牌牌型基线),rebuy 前置桩用。

从【空桌帧】抽每座每区的 _avg_hash_64(与 card_marker/avatar live 同一套 hash,防 harness/live 漂移),
建二元基线参考:live 区域 hamming ≤ 阈 → 像空桌(空座 / 无 +xx);> 阈 → 占位 / +xx 出现。
输出 rois/empty_refs_<profile>.json(很小,**push 它,别 push 帧**)。

复用 pipeline.orchestrator._avg_hash_64 / _hamming(单一实现,builder=live 同源)。
⚠️ cv2 + 导入 orchestrator,**Win-only**;Linux 仅验语法 + 取众数/稳定性逻辑。
用法(Win,空桌截图后):
  python tools\\build_empty_refs.py --frames-dir "data\\recordings\\<ts>\\frames" --profile party_poker_8 --dump
"""
import argparse
import glob
import json
import logging
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.orchestrator import _avg_hash_64, _hamming  # noqa: E402  单一实现,防漂移

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_empty_refs")

# 采这些区的空桌基线(占用看 fold/stack/id;+xx 看 win_amount;摊牌牌型 hand_type;card_marker 兜底)
REGIONS = ["fold_area", "stack_area", "id_area", "amount_area",
           "win_amount_area", "hand_type_area", "card_marker"]


def str_majority(hashes):
    """多帧 64-char "0/1" 串按位取众数 → 稳定参考(治单帧瞬态)。"""
    if not hashes:
        return None
    n, L = len(hashes), len(hashes[0])
    return "".join("1" if sum(h[i] == "1" for h in hashes) * 2 > n else "0" for i in range(L))


def crop(frame, roi):
    if roi is None:
        return None
    H, W = frame.shape[:2]
    if roi.left < 0 or roi.top < 0 or roi.left + roi.width > W or roi.top + roi.height > H or roi.width < 2:
        return None
    return frame[roi.top:roi.top + roi.height, roi.left:roi.left + roi.width]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--out", default=None)
    ap.add_argument("--dump", action="store_true", help="存放大空桌抠图,眼验是不是真空桌")
    args = ap.parse_args()

    from capture.roi import ROIManager

    files = sorted(glob.glob(os.path.join(args.frames_dir, "*.png")))
    if not files:
        log.error(f"{args.frames_dir} 下没 *.png"); sys.exit(2)
    mgr = ROIManager.from_json(os.path.join("rois", f"{args.profile}.json"))
    seats = {sr.seat_index: sr for sr in mgr.rois.seat_regions}
    frames = [f for f in (cv2.imread(p) for p in files) if f is not None]
    log.info(f"{len(frames)} 帧 × {len(seats)} 座")

    dbg = os.path.join("tools", "output", "empty_refs")
    if args.dump:
        os.makedirs(dbg, exist_ok=True)
        for old in glob.glob(os.path.join(dbg, "*.png")):
            os.remove(old)

    refs = {}  # seat -> region -> {hash, stab, n}
    for sidx, sr in sorted(seats.items()):
        cell = {}
        for region in REGIONS:
            roi = getattr(sr, region, None)
            hs, last = [], None
            for fr in frames:
                c = crop(fr, roi)
                if c is None or c.size == 0:
                    continue
                hs.append(_avg_hash_64(c)); last = c
            if not hs:
                continue
            ref = str_majority(hs)
            stab = max(_hamming(h, ref) for h in hs)  # 各帧离众数最大 hamming(空桌静止应≈0)
            cell[region] = {"hash": ref, "stab": stab, "n": len(hs)}
            if args.dump and last is not None:
                cv2.imwrite(os.path.join(dbg, f"s{sidx}_{region}_stab{stab}.png"),
                            cv2.resize(last, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))
        if cell:
            refs[str(sidx)] = cell

    out = args.out or os.path.join("rois", f"empty_refs_{args.profile}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"profile": args.profile, "n_frames": len(frames),
                   "regions": REGIONS, "seats": refs}, f, ensure_ascii=False, indent=2)

    print("\n===== 空桌参考稳定性(各帧离众数最大 hamming,越小越稳;大=该区帧间有变,慎用)=====")
    for sidx in sorted(refs, key=int):
        print(f"  seat{sidx}: " + ", ".join(f"{r}:{c['stab']}" for r, c in refs[sidx].items()))
    log.info(f"✅ 写 {out} —— **push 这个 JSON,别 push 帧**。--dump 图在 {dbg}/ 眼验是不是真空桌")


if __name__ == "__main__":
    main()
