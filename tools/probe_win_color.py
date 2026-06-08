"""tools/probe_win_color.py — #241 +xx 黄色特异检测离线验(替 avg_hash 二元)。

avg_hash 认"区域变了没"→ 对 s0 任何亮度/纹理变化都误报。+xx 数字是【明显黄色】→
改测 win_amount 区【黄色像素占比】:无 +xx≈0,+xx 出现→高。本探针验它能否干净二元 + 治 s0。

每帧每座算黄色占比(HSV:H∈[hlo,hhi] & S>smin & V>vmin)→ 直方图(应 bimodal)+ per阈命中率 +
每座 min/中位/max(s0 正常应≈0,若仍高=黄不是 s0 的误报源,得另查)+ dump 高/低占比抠图眼标。
⚠️ cv2,Win-only。
用法(Win):
  python tools\\probe_win_color.py --frames-dir "data\\recordings\\<ts>\\frames" --profile party_poker_8 --dump
"""
import argparse
import glob
import logging
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("probe_win_color")


def crop(frame, roi):
    if roi is None:
        return None
    H, W = frame.shape[:2]
    if roi.left < 0 or roi.top < 0 or roi.left + roi.width > W or roi.top + roi.height > H or roi.width < 2:
        return None
    return frame[roi.top:roi.top + roi.height, roi.left:roi.left + roi.width]


def yellow_frac(bgr, hlo, hhi, smin, vmin):
    """黄色像素占比(HSV)。+xx 黄字 → 高;空台面/牌/聊天(非黄)→ ≈0。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] >= hlo) & (hsv[..., 0] <= hhi) &
         (hsv[..., 1] >= smin) & (hsv[..., 2] >= vmin))
    return float(m.mean())


def hist_bar(counts, labels, width=40):
    mx = max(counts) or 1
    return "\n".join(f"  {lab:>9} | {'█' * int(round(width * c / mx))} {c}" for c, lab in zip(counts, labels))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--hlo", type=int, default=18, help="黄色 H 下限(cv2 0-179)")
    ap.add_argument("--hhi", type=int, default=38, help="黄色 H 上限")
    ap.add_argument("--smin", type=int, default=70)
    ap.add_argument("--vmin", type=int, default=120)
    ap.add_argument("--thresholds", default="0.02,0.05,0.10,0.20", help="黄占比阈(>判 +xx)")
    ap.add_argument("--max-frames", type=int, default=400)
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    from capture.roi import ROIManager

    files = sorted(glob.glob(os.path.join(args.frames_dir, "*.png")))
    if not files:
        log.error(f"{args.frames_dir} 下没 *.png"); sys.exit(2)
    if len(files) > args.max_frames:
        files = [files[i] for i in np.linspace(0, len(files) - 1, args.max_frames).astype(int)]
    mgr = ROIManager.from_json(os.path.join("rois", f"{args.profile}.json"))
    seats = {sr.seat_index: sr for sr in mgr.rois.seat_regions}
    ths = [float(t) for t in args.thresholds.split(",")]
    log.info(f"扫 {len(files)} 帧 × {len(seats)} 座  黄色 H[{args.hlo},{args.hhi}] S>{args.smin} V>{args.vmin}")

    allv = []                 # 全部黄占比
    by_seat = {}              # seat -> [frac]
    samples = []              # (frac, crop, fname, seat)
    for fp in files:
        frame = cv2.imread(fp)
        if frame is None:
            continue
        fn = os.path.basename(fp)[:-4]
        for sidx, sr in seats.items():
            c = crop(frame, getattr(sr, "win_amount_area", None))
            if c is None or c.size == 0:
                continue
            yf = yellow_frac(c, args.hlo, args.hhi, args.smin, args.vmin)
            allv.append(yf); by_seat.setdefault(sidx, []).append(yf)
            if args.dump:
                samples.append((yf, c, fn, sidx))

    arr = np.array(allv)
    bins = [0, .005, .01, .02, .04, .07, .10, .15, .25, .40, 1.01]
    hc, _ = np.histogram(arr, bins=bins)
    hl = [f"{bins[i]:.3f}-{bins[i+1]:.2f}" for i in range(len(bins) - 1)]
    print(f"\n{'='*60}\nwin_amount 黄色占比分布 n={arr.size}(应 bimodal:无xx≈0 / +xx 高)\n{'='*60}")
    print(hist_bar(hc.tolist(), hl))
    for th in ths:
        print(f"  @阈{th}: 判+xx {(arr > th).mean()*100:.1f}%")
    print("\n每座 黄占比 [min/中位/max](s0 正常应≈0;若 s0 仍偏高=黄非其误报源):")
    for s in sorted(by_seat):
        v = np.array(by_seat[s])
        print(f"  s{s}: [{v.min():.3f}/{np.median(v):.3f}/{v.max():.3f}]")
    if args.dump and samples:
        outdir = os.path.join("tools", "output", "win_color")
        os.makedirs(outdir, exist_ok=True)
        for old in glob.glob(os.path.join(outdir, "*.png")):
            os.remove(old)
        ss = sorted(samples, key=lambda t: t[0])
        for tag, grp in (("low", ss[:8]), ("high", ss[-8:])):
            for yf, c, fn, sidx in grp:
                cv2.imwrite(os.path.join(outdir, f"{tag}_y{yf:.3f}_s{sidx}_{fn}.png"),
                            cv2.resize(c, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))
        print(f"\ndump 最低8+最高8 → {outdir}/(high 应见黄色 +数字;low 应无黄)")
    print("\n判读:bimodal + 高簇是真 +xx + s0 正常帧黄≈0 ⟹ 黄色检测可替 avg_hash 治 s0 误报。")


if __name__ == "__main__":
    main()
