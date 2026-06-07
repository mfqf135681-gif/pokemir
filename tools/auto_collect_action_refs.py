"""tools/auto_collect_action_refs.py — #240 自动每座采集动作参考(多参考,治跟注跨座漂移)。

根因(探针实测):单参考 text_shape_hash 跨座漂移大(尤其跟注:别座落 hamming 12-20 > 阈10 → 漏)。
解:每(座,动作)各采一个参考。本工具用少量【种子锚】(每动作 1 个)把全录像各座 crop 分到最近动作
(松阈值 assign-th 容跨座漂移),每(座,动作)挑"最像种子"的代表存参考,dump 放大图供眼确认。

输出 rois/action_refs_<profile>.json(多参考/动作);live ActionPhashReader 读它,match 取 min hamming。
复用 pipeline.action_phash 的 text_shape_hash / hamming / LABEL_TO_WORD(单一实现,防漂移)。

⚠️ cv2 Win-only;Linux 仅验语法 + 分配/挑选逻辑。
用法(Win):
  python tools\\auto_collect_action_refs.py --frames-dir "data\\recordings\\<ts>\\frames" --profile party_poker_8 \\
      --seed-anchors "f_000097:0:check,f_001028:7:raise,f_001028:6:call,f_002175:6:bet" --dump
  审 tools\\output\\action_refs_review\\ 各座各动作放大图;错的用 --exclude "3:call,5:bet" 重跑剔除。
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
from pipeline.action_phash import text_shape_hash, hamming, LABEL_TO_WORD  # noqa: E402  单一实现

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("auto_collect_action_refs")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True, nargs="+",
                    help="一个或多个录像帧目录(多段一起扫采齐各座各动作);种子锚从第一个目录解析")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--seed-anchors", required=True, help="每动作 1 个种子锚 frame:seat:label(用于把各座 crop 分到动作)")
    ap.add_argument("--assign-th", type=int, default=20, help="crop 到种子 hamming ≤ 此 → 归该动作(容跨座漂移;须 < 类间~24)")
    ap.add_argument("--refs-per-cell", type=int, default=1, help="每(座,动作)取几个代表(按到种子 hamming 升序)")
    ap.add_argument("--max-frames", type=int, default=600)
    ap.add_argument("--sat-th", type=int, default=60)
    ap.add_argument("--val-th", type=int, default=100)
    ap.add_argument("--grid", type=int, default=16, help="hash 网格(8粗/16细,分加注下注)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--threshold", type=int, default=12, help="输出 refs 的 live match 阈值(多参考后可比单参考略松)")
    ap.add_argument("--margin", type=int, default=0)
    ap.add_argument("--exclude", default="", help="逗号分隔 seat:label 剔除眼确认错的(如 3:call,5:bet)")
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    from capture.roi import ROIManager

    files = []
    for d in args.frames_dir:
        files += sorted(glob.glob(os.path.join(d, "*.png")))
    if not files:
        log.error(f"{args.frames_dir} 下没 *.png"); sys.exit(2)
    seed_files = sorted(glob.glob(os.path.join(args.frames_dir[0], "*.png")))  # 种子锚从第一目录解析(避免跨段同名碰撞)
    log.info(f"{len(args.frames_dir)} 个目录共 {len(files)} 帧")
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

    # 1) 种子参考(每动作 1 个)
    seeds = {}  # word -> seed hash
    for tok in args.seed_anchors.split(","):
        tok = tok.strip()
        if not tok:
            continue
        fsub, seat_s, label = tok.split(":")
        word = LABEL_TO_WORD[label]
        fp = next((f for f in seed_files if fsub in os.path.basename(f)), None)
        if fp is None:
            log.error(f"种子锚帧在第一目录没找到: {fsub}"); sys.exit(2)
        h = text_shape_hash(crop_action(cv2.imread(fp), int(seat_s)), args.sat_th, args.val_th, args.grid)
        if not h:
            log.error(f"种子锚 {tok} 抠不出文字"); sys.exit(2)
        seeds[word] = h
        log.info(f"种子 [{label}→{word}] ← {os.path.basename(fp)} seat{seat_s}")
    excl = set(args.exclude.split(",")) if args.exclude else set()  # "seat:label"

    # 2) 扫全录像,每 crop 分到最近种子动作(≤ assign-th)
    sampled = files if len(files) <= args.max_frames else \
        [files[i] for i in np.linspace(0, len(files) - 1, args.max_frames).astype(int)]
    log.info(f"扫 {len(sampled)} 帧 × {len(seat_roi)} 座")
    # cell[(seat, word)] = list of (hamming_to_seed, hash, crop, fname)
    cell = {}
    for fp in sampled:
        frame = cv2.imread(fp)
        if frame is None:
            continue
        for sidx in seat_roi:
            c = crop_action(frame, sidx)
            if c is None:
                continue
            h = text_shape_hash(c, args.sat_th, args.val_th, args.grid)
            if not h:
                continue
            best_w, best_d = None, 999
            for w, sh in seeds.items():
                d = hamming(h, sh)
                if d < best_d:
                    best_d, best_w = d, w
            if best_w is not None and best_d <= args.assign_th:
                cell.setdefault((sidx, best_w), []).append((best_d, h, c, os.path.basename(fp)[:-4]))

    # 3) 每(座,动作)挑代表(到种子 hamming 升序取前 refs-per-cell)+ dump 审
    out_refs = {}
    review = os.path.join("tools", "output", "action_refs_review")
    if args.dump:
        os.makedirs(review, exist_ok=True)
        for old in glob.glob(os.path.join(review, "*.png")):
            os.remove(old)
    WORD_TO_LABEL = {v: k for k, v in LABEL_TO_WORD.items()}
    coverage = {}
    for (sidx, word), lst in sorted(cell.items()):
        label = WORD_TO_LABEL.get(word, word)
        if f"{sidx}:{label}" in excl:
            continue
        lst.sort(key=lambda t: t[0])
        picks = lst[:args.refs_per_cell]
        out_refs.setdefault(word, []).extend(p[1] for p in picks)
        coverage.setdefault(word, []).append(sidx)
        if args.dump:
            for k, (d, _h, c, fn) in enumerate(picks):
                big = cv2.resize(c, None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST)
                cv2.imwrite(os.path.join(review, f"{label}_seat{sidx}_h{d:02d}_{fn}.png"), big)

    # 4) 写 refs + 报覆盖
    out = args.out or os.path.join("rois", f"action_refs_{args.profile}.json")
    payload = {"version": 1, "profile": args.profile, "auto_collected": True,
               "sat_th": args.sat_th, "val_th": args.val_th, "grid": args.grid,
               "match_threshold": args.threshold, "margin": args.margin, "refs": out_refs}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("\n========== 覆盖(每动作采到哪些座的参考)==========")
    for word in seeds:
        cov = sorted(coverage.get(word, []))
        miss = [s for s in sorted(seat_roi) if s not in cov]
        print(f"  {WORD_TO_LABEL[word]:>6} ({word}): {len(out_refs.get(word, []))} 参考  座 {cov}  缺座 {miss}")
    log.info(f"✅ 写 {out}(多参考);阈值={args.threshold}")
    if args.dump:
        log.info(f"审图 → {review}/(按 动作_seat 看;读字确认每张是该动作;错的 --exclude 'seat:label' 重跑)")


if __name__ == "__main__":
    main()
