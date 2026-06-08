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


def yellow_metrics(bgr, hlo, hhi, smin, vmin):
    """黄色(HSV)→ (占比, 数量)。占比=黄/框面积(大框稀释);数量=黄像素绝对数(框无关,治稀释)。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] >= hlo) & (hsv[..., 0] <= hhi) &
         (hsv[..., 1] >= smin) & (hsv[..., 2] >= vmin))
    return float(m.mean()), int(m.sum())


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
    ap.add_argument("--count-ths", default="30,60,100,160", help="黄数量阈(>判 +xx;框无关)")
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

    fracs, counts = [], []    # 全部 (占比, 数量)
    by_seat = {}              # seat -> [(frac,count)]
    samples = []              # (count, frac, crop, fname, seat)
    for fp in files:
        frame = cv2.imread(fp)
        if frame is None:
            continue
        fn = os.path.basename(fp)[:-4]
        for sidx, sr in seats.items():
            c = crop(frame, getattr(sr, "win_amount_area", None))
            if c is None or c.size == 0:
                continue
            yf, yc = yellow_metrics(c, args.hlo, args.hhi, args.smin, args.vmin)
            fracs.append(yf); counts.append(yc)
            by_seat.setdefault(sidx, []).append((yf, yc))
            if args.dump:
                samples.append((yc, yf, c, fn, sidx))

    farr, carr = np.array(fracs), np.array(counts)
    cths = [int(round(t)) for t in args.count_ths.split(",")]
    # ① 占比分布
    fbins = [0, .005, .01, .02, .04, .07, .10, .15, .25, .40, 1.01]
    fh, _ = np.histogram(farr, bins=fbins)
    fl = [f"{fbins[i]:.3f}-{fbins[i+1]:.2f}" for i in range(len(fbins) - 1)]
    print(f"\n{'='*60}\n①占比分布 n={farr.size}(无xx≈0/+xx高;框大被稀释)\n{'='*60}")
    print(hist_bar(fh.tolist(), fl))
    for th in ths:
        print(f"  @占比阈{th}: 判+xx {(farr > th).mean()*100:.1f}%")
    # ② 数量分布(框无关,治稀释 → +xx 簇离 0 更远 = margin 大)
    cbins = [0, 5, 15, 30, 60, 100, 160, 250, 400, 700, 99999]
    ch, _ = np.histogram(carr, bins=cbins)
    cl = [f"{cbins[i]}-{cbins[i+1]}" for i in range(len(cbins) - 1)]
    print(f"\n{'='*60}\n②数量分布 n={carr.size}(黄像素绝对数,框无关;看 +xx 簇离 0 margin)\n{'='*60}")
    print(hist_bar(ch.tolist(), cl))
    for th in cths:
        print(f"  @数量阈{th}: 判+xx {(carr > th).mean()*100:.1f}%")
    print("\n每座 [占比 min/中位/max] | [数量 min/中位/max](s0 正常应≈0):")
    for s in sorted(by_seat):
        f = np.array([x[0] for x in by_seat[s]]); cc = np.array([x[1] for x in by_seat[s]])
        print(f"  s{s}: [{f.min():.3f}/{np.median(f):.3f}/{f.max():.3f}] | "
              f"[{int(cc.min())}/{int(np.median(cc))}/{int(cc.max())}]")
    if args.dump and samples:
        outdir = os.path.join("tools", "output", "win_color")
        os.makedirs(outdir, exist_ok=True)
        for old in glob.glob(os.path.join(outdir, "*.png")):
            os.remove(old)
        ss = sorted(samples, key=lambda t: t[0])  # 按数量排
        for tag, grp in (("low", ss[:8]), ("high", ss[-8:])):
            for yc, yf, c, fn, sidx in grp:
                cv2.imwrite(os.path.join(outdir, f"{tag}_c{yc:04d}_y{yf:.3f}_s{sidx}_{fn}.png"),
                            cv2.resize(c, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))
        print(f"\ndump 数量最低8+最高8 → {outdir}/(high 应见黄色 +数字;low 应无黄)")
    print("\n判读:对比①②哪个【+xx 簇离 0 的空隙更宽】= margin 大 = 漏报余地小。数量若 margin 明显更大 → live 换数量判定。")


if __name__ == "__main__":
    main()
