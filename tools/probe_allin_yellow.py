r"""tools/probe_allin_yellow.py — all-in 黄色识别桩 离线可分性验(独立信号,圈梁只校不建)。

用户活观察(2026-06-09):all-in 后 fold_text 区出现【黄色粗体 "allin" 两字】(+头像换色闪烁边框)。
本探针验:fold_text 区【黄色像素计数】能否干净分出三类 ——
  ① 真 all-in(黄 "allin" 两字)   = 中等黄计数(落 [lo, hi] 带);
  ② 非 all-in(头像/弃牌白字/timer白字)= 黄计数 ≈ 0(白字非黄、普通头像非黄);
  ③ 黄头像(对抗 case,用户强调)   = 整框黄,巨量计数 → 上界 hi 拒。
→ 若 ①②③ 三簇离得开 + 上界 hi 能卡掉 ③,则 fold_text 黄计数([lo,hi] 双阈)= 可靠独立 all-in 桩。

黄色 HSV 参数 + 计数算法 **复用 +xx 的 probe_win_color.yellow_metrics**(单一实现,防 harness/live 漂移)。
区域用 **fold_text**(用户眼验:已框好、和 allin 区重叠、比 fold_area 紧 → 头像污染小)。
⚠️ cv2,Win-only(本 Linux 机无 cv2 不可跑;算法纯参数,Win 实测)。

用法(Win):
  python tools\probe_allin_yellow.py --frames-dir "data\recordings\<ts>\frames" --profile party_poker_8 --dump
判读:看②黄计数分布——真 all-in 那一簇离 0 多远(margin)、黄头像那一簇在哪(定 hi);
      dump 高计数抠图你眼标(真 allin / 黄头像 / 误触)→ 定 [lo, hi]。
"""
import argparse
import glob
import json
import logging
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 同目录 import probe_win_color

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("probe_allin_yellow")

# 单一实现:黄色计数复用 +xx 探针(同 HSV 参数 18/38/70/120,防漂移)
from probe_win_color import yellow_metrics  # noqa: E402


def crop(frame, box):
    """box=[l,t,w,h] → 帧内安全裁剪;越界/过小 → None。"""
    if not box or len(box) != 4:
        return None
    l, t, w, h = box
    H, W = frame.shape[:2]
    if l < 0 or t < 0 or l + w > W or t + h > H or w < 2 or h < 2:
        return None
    return frame[t:t + h, l:l + w]


def hist_bar(counts, labels, width=44):
    mx = max(counts) or 1
    return "\n".join(f"  {lab:>10} | {'█' * int(round(width * c / mx))} {c}" for c, lab in zip(counts, labels))


def main():
    ap = argparse.ArgumentParser(description="all-in 黄色识别桩可分性验(fold_text 区黄计数)")
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--region", default="fold_text", help="扫哪个 ROI 区(默认 fold_text;可换 fold_area 对照)")
    ap.add_argument("--hlo", type=int, default=18, help="黄 H 下限(cv2 0-179)")
    ap.add_argument("--hhi", type=int, default=38)
    ap.add_argument("--smin", type=int, default=70)
    ap.add_argument("--vmin", type=int, default=120)
    ap.add_argument("--count-ths", default="20,50,100,200,400", help="黄计数阈(>判候选;定 [lo,hi] 用)")
    ap.add_argument("--lo", type=int, default=400, help="all-in 候选带下界(初定 400)")
    ap.add_argument("--hi", type=int, default=1100, help="all-in 候选带上界(>hi 疑黄头像)")
    ap.add_argument("--max-frames", type=int, default=600, help="全扫设大(如 20000)即不抽样")
    ap.add_argument("--dump", action="store_true", help="dump 高/中/低黄计数抠图供眼标")
    args = ap.parse_args()

    prof_path = os.path.join("rois", f"{args.profile}.json")
    if not os.path.isfile(prof_path):
        log.error(f"找不到 profile {prof_path}"); sys.exit(2)
    prof = json.load(open(prof_path, encoding="utf-8"))
    seats = {s["seat_index"]: s.get(args.region) for s in prof["seats"]}
    seats = {i: b for i, b in seats.items() if b}  # 只留有该区框的座
    if not seats:
        log.error(f"profile 里没有 {args.region} 框"); sys.exit(2)

    files = sorted(glob.glob(os.path.join(args.frames_dir, "*.png")))
    if not files:
        log.error(f"{args.frames_dir} 下没 *.png"); sys.exit(2)
    if len(files) > args.max_frames:
        files = [files[i] for i in np.linspace(0, len(files) - 1, args.max_frames).astype(int)]
    log.info(f"扫 {len(files)} 帧 × {len(seats)} 座  区={args.region}  黄 H[{args.hlo},{args.hhi}] S>{args.smin} V>{args.vmin}")

    counts = []                  # 全部黄计数
    by_seat = {}                 # seat -> [count]
    samples = []                 # (count, crop_img, fname, seat)
    for fp in files:
        frame = cv2.imread(fp)
        if frame is None:
            continue
        fn = os.path.basename(fp)[:-4]
        for sidx, box in seats.items():
            c = crop(frame, box)
            if c is None or c.size == 0:
                continue
            _, ycount = yellow_metrics(c, args.hlo, args.hhi, args.smin, args.vmin)
            counts.append(ycount)
            by_seat.setdefault(sidx, []).append(ycount)
            if args.dump:
                samples.append((ycount, c, fn, sidx))

    carr = np.array(counts)
    cths = [int(t) for t in args.count_ths.split(",")]
    cbins = [0, 5, 20, 50, 100, 200, 400, 700, 1200, 99999]
    ch, _ = np.histogram(carr, bins=cbins)
    cl = [f"{cbins[i]}-{cbins[i+1]}" for i in range(len(cbins) - 1)]
    print(f"\n{'='*62}\n黄计数分布 n={carr.size}(区={args.region};找 ①真allin簇 / ③黄头像簇 离 0 的位置)\n{'='*62}")
    print(hist_bar(ch.tolist(), cl))
    for th in cths:
        print(f"  @计数阈 {th}: 超阈占 {(carr > th).mean()*100:.2f}%")
    print("\n每座 黄计数 [min/中位/max](普通座大多≈0;某座 max 高=该座出现过 allin 或黄头像):")
    for s in sorted(by_seat):
        a = np.array(by_seat[s])
        print(f"  s{s}: [{int(a.min())}/{int(np.median(a))}/{int(a.max())}]")

    # 候选带统计(本次扫的关键判据)
    in_band = (carr >= args.lo) & (carr <= args.hi)
    over_hi = carr > args.hi
    sub = (carr >= 20) & (carr < args.lo)
    print(f"\n{'='*62}\n候选带判据(lo={args.lo} hi={args.hi})\n{'='*62}")
    print(f"  ① [lo,hi] 候选 all-in : {int(in_band.sum()):5d} 帧座 ({in_band.mean()*100:.2f}%)")
    print(f"  ② >hi 疑黄头像/超       : {int(over_hi.sum()):5d} 帧座 ({over_hi.mean()*100:.2f}%)  ← 应≈0,>0 必眼标")
    print(f"  ③ [20,lo) 阈下非零      : {int(sub.sum()):5d} 帧座 ({sub.mean()*100:.2f}%)  ← 看是否藏漏掉的 all-in")

    if args.dump and samples:
        outdir = os.path.join("tools", "output", "allin_yellow")
        os.makedirs(outdir, exist_ok=True)
        for old in glob.glob(os.path.join(outdir, "*.png")):
            os.remove(old)

        def pick(lo, hi, n):  # [lo,hi) 计数带里按计数均匀取 n 个(覆盖该带,非单点)
            grp = sorted((s for s in samples if lo <= s[0] < hi), key=lambda t: t[0])
            if not grp:
                return []
            idx = np.linspace(0, len(grp) - 1, min(n, len(grp))).astype(int)
            return [grp[i] for i in sorted(set(idx))]

        groups = (
            ("over", pick(args.hi, 10**9, 12)),       # 超上界:疑黄头像(应没有)
            ("band", pick(args.lo, args.hi, 14)),     # 候选 all-in 带:应全是真 allin
            ("sub",  pick(20, args.lo, 10)),          # 阈下非零:有没有漏的 all-in / 是什么黄
            ("zero", pick(0, 6, 6)),                  # 基线对照
        )
        for tag, grp in groups:
            for yc, c, fn, sidx in grp:
                cv2.imwrite(os.path.join(outdir, f"{tag}_y{yc:05d}_s{sidx}_{fn}.png"),
                            cv2.resize(c, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))
        print(f"\ndump → {outdir}/(over/band/sub/zero 四组)")
        print("  眼标:band 是否【全是真 allin】? over 是否出现【黄头像】(整框糊黄)? sub 里有没有【漏掉的 allin】?")
    print("\n判读:band 全 allin + over≈0(无黄头像)+ sub 无漏 → [lo,hi] 双阈跨录像成立,可接 live 桩。")


if __name__ == "__main__":
    main()
