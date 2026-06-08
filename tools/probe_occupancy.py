"""tools/probe_occupancy.py — #241 占用判定离线验区分度(像 probe_action_phash 那样先录像验)。

拿空桌基线(rois/empty_refs_<profile>.json)打一段录像:每帧每座每区算
hamming(live 区域 _avg_hash_64, 空桌基线)。占位应远 > 阈、空座应 ≈0。
输出:① 每区 hamming 分布直方图(应 bimodal:空簇≈0 / 占位簇高)② 各阈值下判"占"占比
③ 每座每区 min/中位/max ④ dump 最低(像空)/最高(像占)抠图眼标。

bimodal + 空占簇间有空隙 + dump 眼标对 ⟹ 该区可作占用判定;某区占位时也常≈空 → 区分度差,剔。
复用 pipeline.orchestrator._avg_hash_64 / _hamming(单一实现,probe=live 同源)。
⚠️ cv2 + 导入 orchestrator,Win-only。
用法(Win):
  python tools\\probe_occupancy.py --frames-dir "data\\recordings\\<ts>\\frames" --profile party_poker_8 \\
      --refs-json "rois\\empty_refs_party_poker_8.json" --dump
"""
import argparse
import glob
import logging
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.orchestrator import _avg_hash_64, _hamming  # noqa: E402  单一实现

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("probe_occupancy")


def hist_bar(counts, labels, width=40):
    mx = max(counts) or 1
    return "\n".join(f"  {lab:>7} | {'█' * int(round(width * c / mx))} {c}" for c, lab in zip(counts, labels))


def crop(frame, roi):
    if roi is None:
        return None
    H, W = frame.shape[:2]
    if roi.left < 0 or roi.top < 0 or roi.left + roi.width > W or roi.top + roi.height > H or roi.width < 2:
        return None
    return frame[roi.top:roi.top + roi.height, roi.left:roi.left + roi.width]


def main():
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--refs-json", default=None, help="默认 rois/empty_refs_<profile>.json")
    ap.add_argument("--regions", default="fold_area,stack_area,id_area", help="逗号分隔,验哪些区")
    ap.add_argument("--thresholds", default="8,12,20,30")
    ap.add_argument("--max-frames", type=int, default=400)
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    from capture.roi import ROIManager

    refp = args.refs_json or os.path.join("rois", f"empty_refs_{args.profile}.json")
    with open(refp, encoding="utf-8") as f:
        empty = json.load(f).get("seats", {})
    regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    ths = [int(t) for t in args.thresholds.split(",")]

    files = sorted(glob.glob(os.path.join(args.frames_dir, "*.png")))
    if not files:
        log.error(f"{args.frames_dir} 下没 *.png"); sys.exit(2)
    if len(files) > args.max_frames:
        files = [files[i] for i in np.linspace(0, len(files) - 1, args.max_frames).astype(int)]
    mgr = ROIManager.from_json(os.path.join("rois", f"{args.profile}.json"))
    seats = {sr.seat_index: sr for sr in mgr.rois.seat_regions}
    log.info(f"扫 {len(files)} 帧 × {len(seats)} 座 × {len(regions)} 区  基线={os.path.basename(refp)}")

    # region -> list(hamming);  (seat,region) -> list;  存样本给 dump
    by_region = {r: [] for r in regions}
    by_cell = {}            # (seat,region) -> [ham]
    samples = {r: [] for r in regions}  # (ham, crop, fname, seat)
    for fp in files:
        frame = cv2.imread(fp)
        if frame is None:
            continue
        fn = os.path.basename(fp)[:-4]
        for sidx, sr in seats.items():
            refs = empty.get(str(sidx))
            if not refs:
                continue
            for region in regions:
                ref = refs.get(region)
                c = crop(frame, getattr(sr, region, None))
                if not ref or c is None or c.size == 0:
                    continue
                h = _hamming(_avg_hash_64(c), ref["hash"])
                by_region[region].append(h)
                by_cell.setdefault((sidx, region), []).append(h)
                if args.dump:
                    samples[region].append((h, c, fn, sidx))

    hbins = list(range(0, 61, 4)) + [64]
    hl = [f"{hbins[i]:>2}-{hbins[i+1]:>2}" for i in range(len(hbins) - 1)]
    for region in regions:
        arr = np.array(by_region[region])
        if arr.size == 0:
            print(f"\n=== {region}: 无数据 ==="); continue
        print(f"\n{'='*60}\n区 [{region}]  n={arr.size}  到空桌基线 hamming 分布(应 bimodal:空≈0/占位高)\n{'='*60}")
        hc, _ = np.histogram(arr, bins=hbins)
        print(hist_bar(hc.tolist(), hl))
        for th in ths:
            print(f"  @阈{th}: 判占 {(arr > th).mean()*100:.0f}%  (≤{th} 判空 {(arr <= th).mean()*100:.0f}%)")
        # 每座 min/中位/max:看是否有座"占位也常≈空"(min 很低=区分度差或确有空座)
        print("  每座 [min/中位/max]:", "  ".join(
            f"s{s}[{int(np.min(v))}/{int(np.median(v))}/{int(np.max(v))}]"
            for (s, r), v in sorted(by_cell.items()) if r == region))
        if args.dump and samples[region]:
            outdir = os.path.join("tools", "output", "occupancy", region)
            os.makedirs(outdir, exist_ok=True)
            for old in glob.glob(os.path.join(outdir, "*.png")):
                os.remove(old)
            ss = sorted(samples[region], key=lambda t: t[0])
            for tag, group in (("low", ss[:8]), ("high", ss[-8:])):
                for h, c, fn, sidx in group:
                    cv2.imwrite(os.path.join(outdir, f"{tag}_h{h:02d}_s{sidx}_{fn}.png"),
                                cv2.resize(c, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))
            print(f"  dump 最低8(像空)+最高8(像占) → {outdir}/(眼标:low 真空? high 真占?)")

    print("\n判读:某区 bimodal + 空(≈0)占(高)间有空隙 + dump 眼标对 ⟹ 可作占用判定;"
          "\n      若占位帧也常落低 hamming(≈空)→ 该区区分度差,剔掉换别的区。")


if __name__ == "__main__":
    main()
