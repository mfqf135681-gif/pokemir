"""tools/id_phash.py — 玩家 ID phash 验证(image-only,零 OCR)。

治 OCR 漂移把同玩家拆成多名字(见 find_player_aliases)。名字【像素】稳定、漂移的是 OCR
→ 对 id ROI 做 phash 当稳定 player key。本工具验两档(card_marker bimodal 同款思路):
  ① 同座跨帧稳定:同一玩家坐着不动 → phash 几乎不变(hamming≈0)。
  ② 跨座区分:不同玩家 → phash 两两 hamming 大(分得开)。
  ③ --refs:打各座众数 phash(int)供跨录像/跨座同玩家比对(ground truth 由用户指认)。

⚠️ cv2,Win 端。id ROI 须已跨座算齐(card_marker 锚),否则跨座 phash 必偏。
用法: python tools\id_phash.py --session data\recordings\<ts> [--hash-size 16] [--decimate 50]
"""
import argparse
import json
import os
from collections import Counter
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _phash(crop, size):
    import cv2
    if crop is None or crop.size == 0:
        return 0
    g = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (size, size))
    m = float(g.mean())
    bits = 0
    for v in g.flatten():
        bits = (bits << 1) | (1 if float(v) > m else 0)
    return bits


def _ham(a, b):
    return bin(a ^ b).count("1")


def main():
    ap = argparse.ArgumentParser(description="玩家 ID phash 验证(零 OCR)")
    ap.add_argument("--session", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--hash-size", type=int, default=16, help="phash 边长(16=256bit,名字细节够)")
    ap.add_argument("--decimate", type=int, default=50, help="每 N 帧采一次")
    args = ap.parse_args()

    import cv2
    prof = json.loads((Path(_ROOT) / "rois" / f"{args.profile}.json").read_text(encoding="utf-8"))
    id_rois = {s["seat_index"]: s["id"] for s in prof["seats"] if s.get("id")}
    fdir = Path(args.session) / "frames"
    mlines = (Path(args.session) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    frames = [json.loads(x) for x in mlines[1:] if x.strip()]
    bits = args.hash_size ** 2

    per_seat = {s: [] for s in id_rois}  # seat -> [hash,...]
    for k, d in enumerate(frames):
        if k % args.decimate:
            continue
        img = cv2.imread(str(fdir / d["file"]))
        if img is None:
            continue
        for s, roi in id_rois.items():
            l, t, w, h = roi
            per_seat[s].append(_phash(img[t:t + h, l:l + w], args.hash_size))

    print(f"=== ID phash 验证 ({args.hash_size}x{args.hash_size}={bits}bit, 每{args.decimate}帧)===")
    seat_mode = {}
    print("\n① 同座跨帧稳定(同玩家坐着→maxΔ应≈0;大=换人/空座/读不稳):")
    for s in sorted(per_seat):
        hs = [h for h in per_seat[s] if h]
        if not hs:
            print(f"  s{s}: 无"); continue
        mode = Counter(hs).most_common(1)[0][0]
        seat_mode[s] = mode
        maxham = max(_ham(h, mode) for h in hs)
        print(f"  s{s}: {len(hs)}帧 {len(set(hs))}种hash maxΔ众数={maxham}/{bits}")

    print("\n② 跨座区分(不同玩家→两两 hamming 应大;小=phash撞):")
    ss = sorted(seat_mode)
    pairs = [(ss[i], ss[j], _ham(seat_mode[ss[i]], seat_mode[ss[j]]))
             for i in range(len(ss)) for j in range(i + 1, len(ss))]
    if pairs:
        pairs.sort(key=lambda x: x[2])
        mn = pairs[0]
        print(f"  最小 hamming = {mn[2]}/{bits} (s{mn[0]}↔s{mn[1]});越大越好,接近0=有玩家撞")
        print(f"  最近3对: " + " ".join(f"s{a}↔s{b}:{h}" for a, b, h in pairs[:3]))

    print("\n③ 各座众数 phash(int,跨录像/跨座同玩家比对用):")
    for s in ss:
        print(f"  s{s}: {seat_mode[s]}")


if __name__ == "__main__":
    main()
