"""tools/probe_action_phash.py — #240 逐动作二元验收探针

把"动作识别"做成 card_marker 式高置信基桩:每动作一个参考 phash,二元判断
(hamming ≤ 阈值 → 该动作;落空 → 无动作)。本探针验"二元命中是否干净":

用户标少量锚帧(frame:seat:action)→ 建每动作参考(_avg_hash_64,与基桩同套)→
扫全录像,对每动作参考算每 crop 的 min-hamming → 输出:
  ① 该参考的 hamming 分布直方图(应 bimodal:该动作实例落低簇 / idle+其他动作落高簇);
  ② 各候选阈值下的命中数 + 命中 crop 的色相分布(该动作的颜色才对);
  ③ dump 命中(低 hamming)放大动作框,眼标"是不是都是这个动作"。
bimodal + 命中色相单一 + 眼标对 ⟹ 该动作=高置信二元桩。

复用 pipeline.orchestrator._avg_hash_64 / _hamming(card_marker 同套,hamming≤8≈同)。
⚠️ 录像帧在 Win;Linux 仅验语法 + 锚解析 + hash/hamming 逻辑。
用法(Win):
  python tools\\probe_action_phash.py --frames-dir "data\\recordings\\<ts>\\frames" --profile party_poker_8 \\
      --anchors "f_000097:0:check,f_000200:3:raise,f_000150:5:call,f_000160:2:bet" --dump
"""

import argparse
import glob
import logging
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.orchestrator import _avg_hash_64, _hamming  # noqa: E402  复用基桩 hash
from pipeline.action_phash import text_shape_hash, text_norm_img  # noqa: E402  单一实现,防 harness/live 漂移

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("probe_action_phash")


def mean_hue(crop):
    """高饱和像素 median 色相(动作色),无则 -1。仅作命中色相上下文。"""
    if crop is None or crop.size == 0:
        return -1.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = hsv[..., 1] > 80
    return float(np.median(hsv[..., 0][mask])) if mask.any() else -1.0


def sat_frac(crop):
    if crop is None or crop.size == 0:
        return 0.0
    return float((cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1] > 80).mean())


# 动作→期望色相区间(cv2 H 0-179):仅自动认座用;下注实测多色,认座请手填 seat
EXPECT_HUE = {"raise": (0, 30), "call": (95, 140), "bet": (95, 140), "check": (45, 95)}


def hist_bar(counts, labels, width=40):
    mx = max(counts) or 1
    return "\n".join(f"  {lab:>9} | {'█' * int(round(width * c / mx))} {c}" for c, lab in zip(counts, labels))


def find_frame(files, substr):
    for f in files:
        if substr in os.path.basename(f):
            return f
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--max-frames", type=int, default=400)
    ap.add_argument("--anchors", required=True,
                    help="逗号分隔 frame_substr:seat:action,如 f_000097:0:check,f_000200:3:raise")
    ap.add_argument("--thresholds", default="6,8,10,12")
    ap.add_argument("--text-mask", action="store_true",
                    help="用色盲+归一化文字形状 hash(治下注多色 + 各座位置不一);否则用 _avg_hash_64")
    ap.add_argument("--sat-text-th", type=int, default=60, help="抠白字的饱和上限(<=判文字)")
    ap.add_argument("--grid", type=int, default=16, help="text-mask hash 网格(8粗/16细,分加注下注;须与参考同)")
    ap.add_argument("--val-text-th", type=int, default=100, help="抠白字的亮度下限(>=判文字)")
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    hashfn = (lambda c: text_shape_hash(c, args.sat_text_th, args.val_text_th, args.grid)) if args.text_mask else _avg_hash_64
    if args.text_mask:
        log.info("hash 模式:色盲+归一化文字形状(text-mask)")

    from capture.roi import ROIManager

    files = sorted(glob.glob(os.path.join(args.frames_dir, "*.png")))
    if not files:
        log.error(f"{args.frames_dir} 下没 *.png"); sys.exit(2)
    mgr = ROIManager.from_json(os.path.join("rois", f"{args.profile}.json"))
    seat_roi = {sr.seat_index: sr.action_area for sr in mgr.rois.seat_regions
                if getattr(sr, "action_area", None) is not None and sr.action_area.width > 3}

    def crop_action(frame, sidx):
        a = seat_roi.get(sidx)
        if a is None:
            return None
        H, W = frame.shape[:2]
        if a.left < 0 or a.top < 0 or a.left + a.width > W or a.top + a.height > H:
            return None
        return frame[a.top:a.top + a.height, a.left:a.left + a.width]

    # 1) 建参考(每动作可多锚)
    refs = {}        # action -> [hash,...]
    ref_crops = {}   # action -> [crop,...](dump 用)
    anchor_frames = set()
    for tok in args.anchors.split(","):
        tok = tok.strip()
        if not tok:
            continue
        parts = tok.split(":")
        if len(parts) != 3:
            log.error(f"锚格式错: {tok!r}(应 frame:seat:action;seat 可填 * 自动认座)"); sys.exit(2)
        fsub, seat_s, act = parts
        fp = find_frame(files, fsub)
        if fp is None:
            log.error(f"锚帧没找到: {fsub}"); sys.exit(2)
        anchor_frames.add(fp)
        frame = cv2.imread(fp)
        if seat_s.isdigit():
            seat = int(seat_s)
        else:
            # 自动认座:按动作期望色相,在该帧找填充+色相匹配的座(取饱和最高=最清晰填充)
            rng = EXPECT_HUE.get(act)
            if rng is None:
                log.error(f"动作 {act!r} 无期望色相表,自动认座失败,请手填 seat"); sys.exit(2)
            cands = []
            for sidx in seat_roi:
                c = crop_action(frame, sidx)
                if c is None:
                    continue
                hue, sf = mean_hue(c), sat_frac(c)
                if rng[0] <= hue <= rng[1] and sf > 0.5:
                    cands.append((sf, sidx, hue))
            if not cands:
                log.error(f"自动认座失败:{os.path.basename(fp)} 无 {act}(色相{rng})的座,手填 seat"); sys.exit(2)
            cands.sort(reverse=True)
            seat = cands[0][1]
            log.info(f"自动认座 [{act}] {os.path.basename(fp)} → seat{seat}"
                     f"(候选 {','.join(f's{s}(h{h:.0f})' for _,s,h in cands)});⚠️同色多座时核对动作词!")
        crop = crop_action(frame, seat)
        if crop is None:
            log.error(f"锚 {tok} 裁不出 crop(座/坐标)"); sys.exit(2)
        refs.setdefault(act, []).append(hashfn(crop))
        ref_crops.setdefault(act, []).append(crop)
        log.info(f"参考 [{act}] ← {os.path.basename(fp)} seat{seat} hue={mean_hue(crop):.0f}")
    log.info(f"动作参考: {{ {', '.join(f'{a}:{len(h)}个' for a,h in refs.items())} }}")
    if args.text_mask:
        empties = [a for a, hs in refs.items() if any(h == "" for h in hs)]
        if empties:
            log.warning(f"⚠️ 这些动作参考抠不出文字(空 hash): {empties} → 调 --sat-text-th/--val-text-th")
        if args.dump:
            dbg = os.path.join("tools", "output", "action_phash", "_ref_textmask")
            os.makedirs(dbg, exist_ok=True)
            for old in glob.glob(os.path.join(dbg, "*.png")):
                os.remove(old)
            for act, cs in ref_crops.items():
                for k, c in enumerate(cs):
                    ni = text_norm_img(c, args.sat_text_th, args.val_text_th)
                    if ni is not None:
                        cv2.imwrite(os.path.join(dbg, f"{act}_{k}.png"),
                                    cv2.resize(ni, None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST))
            log.info(f"参考抠字图 → {dbg}/(眼验 下注/跟注/… 是否抠干净)")

    # 2) 扫全录像(子采样 + 强制纳入锚帧)
    sampled = files if len(files) <= args.max_frames else \
        [files[i] for i in np.linspace(0, len(files) - 1, args.max_frames).astype(int)]
    sampled = sorted(set(sampled) | anchor_frames)
    log.info(f"扫 {len(sampled)} 帧 × {len(seat_roi)} 座")
    crops = []  # (hash, hue, box, fname, seat)
    for fp in sampled:
        frame = cv2.imread(fp)
        if frame is None:
            continue
        for sidx in seat_roi:
            c = crop_action(frame, sidx)
            if c is None:
                continue
            crops.append((hashfn(c), mean_hue(c), c, os.path.basename(fp)[:-4], sidx))
    log.info(f"采到 {len(crops)} 个 action_area crop")

    ths = [int(t) for t in args.thresholds.split(",")]
    hbins = list(range(0, 41, 4)) + [64]

    # 3) 逐动作二元验收
    for act, rhashes in refs.items():
        dists = np.array([min(_hamming(c[0], rh) for rh in rhashes) for c in crops])
        print(f"\n{'='*60}\n动作 [{act}]  参考 {len(rhashes)} 个 — min-hamming 分布(应 bimodal)\n{'='*60}")
        hc, _ = np.histogram(dists, bins=hbins)
        hl = [f"{hbins[i]:>2}-{hbins[i+1]:>2}" for i in range(len(hbins) - 1)]
        print(hist_bar(hc.tolist(), hl))
        for th in ths:
            idx = np.where(dists <= th)[0]
            if len(idx) == 0:
                print(f"  @hamming≤{th}: 0 命中"); continue
            hues = [crops[i][1] for i in idx if crops[i][1] >= 0]
            hb = list(range(0, 181, 15)); hh, _ = np.histogram(hues, bins=hb)
            top = sorted(zip(hh.tolist(), [f"H{hb[i]}" for i in range(len(hb)-1)]), reverse=True)[:3]
            print(f"  @hamming≤{th}: {len(idx)} 命中  色相 top: {', '.join(f'{l}×{c}' for c,l in top if c)}")
            # 命中座分布:验位置归一——单参考若只在自己采集那座命中=位置归一没生效
            _sc = {}
            for i in idx:
                _sc[crops[i][4]] = _sc.get(crops[i][4], 0) + 1
            print(f"      命中座分布: {', '.join(f's{s}×{n}' for s, n in sorted(_sc.items()))}")
        # dump 最近命中(放大动作框,眼标)
        if args.dump:
            outdir = os.path.join("tools", "output", "action_phash", act)
            os.makedirs(outdir, exist_ok=True)
            for old in glob.glob(os.path.join(outdir, "*.png")):
                os.remove(old)
            order = np.argsort(dists)[:16]
            for rank, i in enumerate(order):
                big = cv2.resize(crops[i][2], None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST)
                cv2.imwrite(os.path.join(outdir, f"d{int(dists[i]):02d}_h{int(crops[i][1]):03d}_{crops[i][3]}_s{crops[i][4]}.png"), big)
            print(f"  ④ dump 16 张最近命中 → {outdir}/(名带 hamming d/色相 h/帧/座;眼标'是不是都 {act}')")

    print("\n判读:某动作分布 bimodal(低簇=真实例/高簇=其余)+ 阈值在空隙 + 命中色相单一 + dump 眼标对")
    print("      ⟹ 该动作=高置信二元桩。最紧看 call vs bet(同蓝),它俩参考会不会互相低 hamming。")


if __name__ == "__main__":
    main()
