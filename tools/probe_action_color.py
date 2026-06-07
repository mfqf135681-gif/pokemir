"""tools/probe_action_color.py — #240 色簇分离探针(唯一硬闸)

验证「动作区色彩判别」:动作出现=整区纯色填充(跟注/下注=蓝、加注=橙、让牌=绿),
idle 显玩家 ID + 桌布(桌布也带颜色,但【有纹路】;动作填充【纯色无纹路】——用户洞察)。

每座 action_area 对【高饱和像素(=纯色填充/桌布的有色部分)】算:
  - 主色相 median hue(隔开有色背景与白字)→ 动作类型(橙/蓝/绿);
  - 填充均匀度 = 这些像素 V(亮度)的 std → 纯色填充【低】、桌布纹路【高】(排除白字,比整图 Laplacian 干净);
  - 整图 Laplacian 方差(辅,会被文字边带高)。

输出:① 主色相直方图 ② 填充均匀度(V-std)分布(应 bimodal:纯色填充低/桌布高)
       ③ 低 V-std(纯色填充=真动作)的色相簇 —— 桌布绿被均匀度滤掉,只剩真动作色。
判读:真动作色相成簇(橙/蓝/绿)且【让牌绿与桌布绿被均匀度分开】⟹ 闸过。
--inspect 看指定帧逐座(验已知动作帧,如 f_000097 s0=让牌 应是 低V-std + 绿hue)。

cv2 HSV:H 0-179(蓝≈120/橙≈15/绿≈60),S/V 0-255。
⚠️ 录像帧在 Win;Linux 仅验语法+ROI加载+指标逻辑(合成图)。
用法(Win): python tools\\probe_action_color.py --frames-dir "data\\recordings\\<ts>\\frames" --profile party_poker_8 --dump
       看指定帧: ... --inspect f_000097
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


def crop_metrics(crop_bgr, sat_pixel_th=80):
    """返回 (sat_frac, hue, v_std, lap):
    - sat_frac : 高饱和像素占比(有色背景=填充或桌布)
    - hue      : 高饱和像素 median 色相(动作色/桌布色,排除白字)
    - v_std    : 高饱和像素 V 的 std = 填充均匀度(纯色填充低/桌布纹路高,排除白字)← 主判据
    - lap      : 整图灰度 Laplacian 方差(辅;会被文字边缘带高)
    无高饱和像素 → sat_frac=0, hue=-1, v_std=-1。"""
    if crop_bgr is None or crop_bgr.size == 0:
        return 0.0, -1.0, -1.0, 0.0
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mask = s > sat_pixel_th
    frac = float(mask.mean())
    if frac <= 0:
        return 0.0, -1.0, -1.0, lap
    vm = v[mask]
    return frac, float(np.median(h[mask])), float(np.std(vm)), lap


def sample_region(frame, a, region, sw, sat_th):
    """按 region 取采样区算 metrics。返回 (sf,hue,vstd,lap,crop) 或 None(越界)。
    stripL/R=紧贴 action_area 左/右 sw 宽纯填充条;stripmin=L,R 里 V-std 更低的。"""
    Hf, Wf = frame.shape[:2]
    if a.top < 0 or a.top + a.height > Hf:
        return None
    def take(x0, x1):
        x0c, x1c = max(0, x0), min(Wf, x1)
        if x1c - x0c < 2:
            return None
        return frame[a.top:a.top + a.height, x0c:x1c]
    spans = {"box": (a.left, a.left + a.width),
             "stripL": (a.left - sw, a.left),
             "stripR": (a.left + a.width, a.left + a.width + sw)}
    if region in spans:
        c = take(*spans[region])
        return (*crop_metrics(c, sat_th), c) if c is not None else None
    # stripmin
    cands = []
    for key in ("stripL", "stripR"):
        c = take(*spans[key])
        if c is not None:
            m = crop_metrics(c, sat_th)
            if m[2] >= 0:
                cands.append((m, c))
    if not cands:
        return None
    m, c = min(cands, key=lambda mc: mc[0][2])
    return (*m, c)


def hist_bar(counts, labels, width=40):
    mx = max(counts) or 1
    return "\n".join(f"  {lab:>10} | {'█' * int(round(width * c / mx))} {c}" for c, lab in zip(counts, labels))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True, help="录像帧目录(扫 *.png)")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--max-frames", type=int, default=400)
    ap.add_argument("--sat-pixel-th", type=int, default=80, help="判'有色像素'的饱和阈值")
    ap.add_argument("--vstd-th", type=float, default=18.0, help="填充均匀度阈值:V-std < 判纯色填充(动作)")
    ap.add_argument("--dump", action="store_true", help="dump 低V-std(真动作)样图供眼标")
    ap.add_argument("--inspect", default=None, help="只看文件名含此子串的帧:逐座打印 hue/V-std/lap + 左右纯填充条")
    ap.add_argument("--strip-w", type=int, default=5, help="紧贴 action_area 左/右取的纯填充条宽度(px)")
    ap.add_argument("--probe-region", choices=["box", "stripL", "stripR", "stripmin"], default="box",
                    help="全量扫的采样区:box=紧框(旧/含字污染)/stripL/stripR=纯填充条/stripmin=取L,R里V-std更低的")
    args = ap.parse_args()

    from capture.roi import ROIManager

    files = sorted(glob.glob(os.path.join(args.frames_dir, "*.png")))
    if not files:
        log.error(f"{args.frames_dir} 下没 *.png"); sys.exit(2)
    sampled = files
    if len(files) > args.max_frames:
        sampled = [files[i] for i in np.linspace(0, len(files) - 1, args.max_frames).astype(int)]
    if args.inspect:  # 强制纳入所有匹配 --inspect 的帧(否则被抽样跳过)
        matches = [f for f in files if args.inspect in os.path.basename(f)]
        sampled = sorted(set(sampled) | set(matches))
        log.info(f"--inspect 命中 {len(matches)} 帧,已强制纳入")
    files = sampled
    log.info(f"扫 {len(files)} 帧")

    mgr = ROIManager.from_json(os.path.join("rois", f"{args.profile}.json"))
    seats = [(sr.seat_index, sr.action_area) for sr in mgr.rois.seat_regions
             if getattr(sr, "action_area", None) is not None and sr.action_area.width > 3 and sr.action_area.height > 3]
    log.info(f"{len(seats)} 个 action_area")

    recs = []  # (frame_i, seat, sat_frac, hue, v_std, lap, crop)
    for fi, fp in enumerate(files):
        frame = cv2.imread(fp)
        if frame is None:
            continue
        H, W = frame.shape[:2]
        do_inspect = args.inspect and args.inspect in os.path.basename(fp)
        if do_inspect:
            print(f"\n--- inspect {os.path.basename(fp)} ---")
        for sidx, a in seats:
            if a.left < 0 or a.top < 0 or a.left + a.width > W or a.top + a.height > H:
                continue
            res = sample_region(frame, a, args.probe_region, args.strip_w, args.sat_pixel_th)
            if res is None:
                continue
            sf, hue, vstd, lap, crop = res
            recs.append((fi, sidx, sf, hue, vstd, lap, crop))
            if do_inspect:
                kind = "纯色填充(动作?)" if 0 <= vstd < args.vstd_th else "桌布/ID(idle?)"
                print(f"  seat{sidx}: hue={hue:6.0f} V-std={vstd:7.1f} lap={lap:8.0f} satFrac={sf:.2f} → {kind}")
                # 存 3× 放大图(文字框±自身宽高)+ 原框 → 看纯色填充在文字外延伸多少,好挑'无字污染'区
                idir = os.path.join("tools", "output", "color_probe", "inspect")
                os.makedirs(idir, exist_ok=True)
                ex0, ex1 = max(0, a.left - a.width), min(W, a.left + 2 * a.width)
                ey0, ey1 = max(0, a.top - a.height), min(H, a.top + 2 * a.height)
                stem = os.path.basename(fp)[:-4]
                cv2.imwrite(os.path.join(idir, f"{stem}_seat{sidx}_3x.png"), frame[ey0:ey1, ex0:ex1])
                cv2.imwrite(os.path.join(idir, f"{stem}_seat{sidx}_orig.png"), crop)
                # 左/右纯填充条(派生自 action_area 坐标,无字污染)→ 验哪边落在纯色填充里
                sw = args.strip_w
                for tag, sx0, sx1 in [("L", a.left - sw, a.left), ("R", a.left + a.width, a.left + a.width + sw)]:
                    cx0, cx1 = max(0, sx0), min(W, sx1)
                    if cx1 - cx0 < 2:
                        print(f"      {tag}条: 越界跳过"); continue
                    strip = frame[a.top:a.top + a.height, cx0:cx1]
                    ssf, shue, svstd, _ = crop_metrics(strip, args.sat_pixel_th)
                    print(f"      {tag}条({cx0}-{cx1}): hue={shue:6.0f} V-std={svstd:7.1f} satFrac={ssf:.2f}")
                    cv2.imwrite(os.path.join(idir, f"{stem}_seat{sidx}_strip{tag}.png"), strip)

    if not recs:
        log.error("没采到任何 crop"); sys.exit(2)

    # ① 主色相直方图(全部高饱和 crop:含动作色 + 桌布色)
    allhue = [r[3] for r in recs if r[3] >= 0]
    print("\n========== ① 全部高饱和 crop 的色相直方图(含动作色 + 桌布色)==========")
    hb = list(range(0, 181, 15)); hc, _ = np.histogram(allhue, bins=hb)
    print(hist_bar(hc.tolist(), [f"H{hb[i]:>3}-{hb[i+1]:>3}" for i in range(len(hb) - 1)]))

    # ② 填充均匀度(V-std)分布 —— 应 bimodal:纯色填充低 / 桌布纹路高
    vstds = np.array([r[4] for r in recs if r[4] >= 0])
    print("\n========== ② 填充均匀度(V-std)分布(纯色填充低 / 桌布纹路高,应 bimodal)==========")
    vbins = [0, 5, 10, 15, 18, 22, 26, 30, 40, 60, 1e9]
    vcnt, _ = np.histogram(vstds, bins=vbins)
    vlabels = [f"{vbins[i]:.0f}-{vbins[i+1]:.0f}" for i in range(len(vbins) - 1)]; vlabels[-1] = f">{vbins[-2]:.0f}"
    print(hist_bar(vcnt.tolist(), vlabels))
    act = [r for r in recs if 0 <= r[4] < args.vstd_th and r[3] >= 0]
    print(f"\nV-std < {args.vstd_th}(判纯色填充=真动作): {len(act)} / {len(recs)}")

    # ③ 真动作(低V-std)的色相簇 —— 桌布色被均匀度滤掉,只剩动作色
    print("\n========== ③ 低V-std(纯色填充=真动作)色相直方图(应分蓝≈120/橙≈15/绿≈60)==========")
    if act:
        ah = np.array([r[3] for r in act]); ac, _ = np.histogram(ah, bins=hb)
        print(hist_bar(ac.tolist(), [f"H{hb[i]:>3}-{hb[i+1]:>3}" for i in range(len(hb) - 1)]))
        print("判读:橙/蓝/绿成三簇,且【让牌绿(此处)与桌布绿(被均匀度滤到②高端)分开】⟹ 闸过。")
    else:
        print("⚠️ 没有低V-std crop——看 ② 分布重定 --vstd-th。")

    # ④ dump 低V-std(真动作)样图
    if args.dump and act:
        outdir = os.path.join("tools", "output", "color_probe")
        os.makedirs(outdir, exist_ok=True)
        per_bin = {}
        for r in act:
            per_bin.setdefault(int(r[3] // 15) * 15, []).append(r)
        saved = 0
        for b, rs in sorted(per_bin.items()):
            for r in rs[:8]:
                cv2.imwrite(os.path.join(outdir, f"hue{b:03d}_vstd{int(r[4]):03d}_frame{r[0]:04d}_seat{r[1]}.png"), r[6]); saved += 1
        print(f"\n④ dump {saved} 张【低V-std=真动作】样图 → {outdir}/(名带 hue/vstd/seat;眼标'哪 hue=哪动作')")


if __name__ == "__main__":
    main()
