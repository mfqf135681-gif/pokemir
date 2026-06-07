"""tools/probe_action_color.py — #240 色簇分离探针(唯一硬闸)

验证「动作区色彩判别」可行性:动作出现时整区纯色填充(跟注/下注=蓝、加注=橙、让牌=绿),
idle 显玩家 ID(低饱和)。本探针扫一段录像帧,对每座 action_area:
  - 取【高饱和像素】的主色相(median hue)→ 隔开纯色填充与文字/ID,得"填充色";
  - 填充占比(高饱和像素比例)→ 区分 action(高)vs idle(低)。
输出:① 填充占比分布(应 bimodal:idle低/action高)② action 帧的色相直方图(应分蓝/橙/绿簇)。
并 dump 各色相簇样图供眼标"哪个色 = 哪个动作"。

判读:占比 bimodal + 色相成不重叠簇 ⟹ 色彩判别可行,#240 实装;簇重叠 ⟹ 回去想。
cv2 HSV 标度:H 0-179(蓝≈120 / 橙≈15 / 绿≈60),S/V 0-255。

⚠️ 录像帧在 Win 侧;Linux 仅验语法 + ROI 加载 + HSV 逻辑(合成图)。
用法(Win):
  python tools\\probe_action_color.py --frames-dir "data\\recordings\\<ts>\\frames" --profile party_poker_8 --max-frames 400 --dump
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
log = logging.getLogger("probe_action_color")


def crop_fill_color(crop_bgr, sat_pixel_th=80):
    """返回 (filled_frac, hue, sat_med, val_med):
    高饱和像素(S>sat_pixel_th)= 纯色填充;它们的占比 + median 色相/饱和/明度。
    无高饱和像素 → filled_frac=0, hue=-1。"""
    if crop_bgr is None or crop_bgr.size == 0:
        return 0.0, -1.0, 0.0, 0.0
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = s > sat_pixel_th
    frac = float(mask.mean())
    if frac <= 0:
        return 0.0, -1.0, 0.0, 0.0
    return frac, float(np.median(h[mask])), float(np.median(s[mask])), float(np.median(v[mask]))


def hist_bar(counts, labels, width=40):
    mx = max(counts) or 1
    out = []
    for c, lab in zip(counts, labels):
        bar = "█" * int(round(width * c / mx))
        out.append(f"  {lab:>10} | {bar} {c}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True, help="录像帧目录(扫 *.png)")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--max-frames", type=int, default=400)
    ap.add_argument("--sat-pixel-th", type=int, default=80, help="判'纯色填充像素'的饱和阈值")
    ap.add_argument("--fill-frac-th", type=float, default=0.5, help="判'action(有填充)'的占比阈值")
    ap.add_argument("--dump", action="store_true", help="dump 各色相簇样图供眼标")
    args = ap.parse_args()

    from capture.roi import ROIManager

    files = sorted(glob.glob(os.path.join(args.frames_dir, "*.png")))
    if not files:
        log.error(f"{args.frames_dir} 下没 *.png"); sys.exit(2)
    if len(files) > args.max_frames:
        # 均匀抽样,别全扫
        idx = np.linspace(0, len(files) - 1, args.max_frames).astype(int)
        files = [files[i] for i in idx]
    log.info(f"扫 {len(files)} 帧")

    mgr = ROIManager.from_json(os.path.join("rois", f"{args.profile}.json"))
    seats = [(sr.seat_index, sr.action_area) for sr in mgr.rois.seat_regions
             if getattr(sr, "action_area", None) is not None and sr.action_area.width > 3 and sr.action_area.height > 3]
    log.info(f"{len(seats)} 个 action_area")

    recs = []  # (frame_i, seat, frac, hue, s, v, crop)
    for fi, fp in enumerate(files):
        frame = cv2.imread(fp)
        if frame is None:
            continue
        H, W = frame.shape[:2]
        for sidx, a in seats:
            if a.left < 0 or a.top < 0 or a.left + a.width > W or a.top + a.height > H:
                continue
            crop = frame[a.top:a.top + a.height, a.left:a.left + a.width]
            frac, hue, s, v = crop_fill_color(crop, args.sat_pixel_th)
            recs.append((fi, sidx, frac, hue, s, v, crop))

    if not recs:
        log.error("没采到任何 crop"); sys.exit(2)

    fracs = np.array([r[2] for r in recs])
    # ① 填充占比 bimodal 检查
    print("\n========== ① 填充占比分布(idle低 / action高,应 bimodal)==========")
    bins = [0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.01]
    cnt, _ = np.histogram(fracs, bins=bins)
    labels = [f"{bins[i]:.2f}-{bins[i+1]:.2f}" for i in range(len(bins) - 1)]
    print(hist_bar(cnt.tolist(), labels))
    n_action = int((fracs >= args.fill_frac_th).sum())
    print(f"\n占比 ≥ {args.fill_frac_th}(判为 action 帧): {n_action} / {len(recs)}")

    # ② action 帧的色相簇
    act = [r for r in recs if r[2] >= args.fill_frac_th and r[3] >= 0]
    print(f"\n========== ② action 帧色相直方图(应分蓝≈120/橙≈15/绿≈60 簇)==========")
    if not act:
        print("⚠️ 没有占比达标的 action 帧——降 --fill-frac-th 或确认这段录像有动作。")
    else:
        hues = np.array([r[3] for r in act])
        hb = list(range(0, 181, 15))
        hcnt, _ = np.histogram(hues, bins=hb)
        hlabels = [f"H{hb[i]:>3}-{hb[i+1]:>3}" for i in range(len(hb) - 1)]
        print(hist_bar(hcnt.tolist(), hlabels))
        print(f"\naction 帧总数: {len(act)};色相直方图峰=候选动作色簇。")

    # ③ dump 各色相簇样图(眼标 哪个色=哪个动作)
    if args.dump and act:
        outdir = os.path.join("tools", "output", "color_probe")
        os.makedirs(outdir, exist_ok=True)
        per_bin = {}
        for r in act:
            b = int(r[3] // 15) * 15
            per_bin.setdefault(b, []).append(r)
        saved = 0
        for b, rs in sorted(per_bin.items()):
            for k, r in enumerate(rs[:8]):  # 每簇最多 8 张
                fn = os.path.join(outdir, f"hue{b:03d}_frame{r[0]:04d}_seat{r[1]}_S{int(r[4])}V{int(r[5])}.png")
                cv2.imwrite(fn, r[6]); saved += 1
        print(f"\n③ dump {saved} 张样图 → {outdir}/(文件名带 hue/seat/SV;眼标'哪个 hue=哪个动作')")


if __name__ == "__main__":
    main()
