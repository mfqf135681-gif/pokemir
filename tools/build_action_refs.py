"""tools/build_action_refs.py — #240 从标注锚帧建动作参考文件(text-shape phash)。

用户标少量锚帧(frame:seat:label,label∈check/raise/call/bet[/fold])→ 每锚裁 action_area →
text_shape_hash → 按中文动作词归组(多锚=多参考)→ 写 rois/action_refs_<profile>.json。
live 由 pipeline.action_phash.ActionPhashReader.load 读它(ACTION_PHASH_LIVE=1)。

复用 pipeline.action_phash.text_shape_hash(单一实现,live/probe/builder 同源,防漂移)。
⚠️ cv2 部分 Win-only;Linux 仅验语法 + 锚解析 + IO。
用法(Win):
  python tools\\build_action_refs.py --frames-dir "data\\recordings\\<ts>\\frames" --profile party_poker_8 \\
      --anchors "f_000097:0:check,f_001028:7:raise,f_001028:6:call,f_002175:6:bet" --threshold 10
"""

import argparse
import glob
import json
import logging
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.action_phash import text_shape_hash, LABEL_TO_WORD  # noqa: E402  单一实现

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_action_refs")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True, nargs="+", help="一个或多个录像帧目录(补锚可跨段;首匹配)")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--anchors", required=True, help="逗号分隔 frame_substr:seat:label(label∈check/raise/call/bet/fold)")
    ap.add_argument("--out", default=None, help="默认 rois/action_refs_<profile>.json")
    ap.add_argument("--threshold", type=int, default=10, help="match hamming 阈值(实测低簇≤6-12,idle 30+)")
    ap.add_argument("--margin", type=int, default=0, help=">0 时次优在 margin 内判歧义→None(交游戏状态)")
    ap.add_argument("--sat-th", type=int, default=60)
    ap.add_argument("--val-th", type=int, default=100)
    ap.add_argument("--grid", type=int, default=16, help="hash 网格(8=64位粗;16=256位细);append 时沿用现有")
    ap.add_argument("--first-char", action=argparse.BooleanOptionalAction, default=True,
                    help="只 hash 第一个字(跟/加/下/过 互异,治加注下注共享'注');append 沿用现有")
    ap.add_argument("--append", action="store_true",
                    help="追加进现有 action_refs JSON(补缺座,不覆盖已采的多座参考)")
    args = ap.parse_args()

    from capture.roi import ROIManager

    files = []
    for d in args.frames_dir:
        files += sorted(glob.glob(os.path.join(d, "*.png")))
    if not files:
        log.error(f"{args.frames_dir} 下没 *.png"); sys.exit(2)
    mgr = ROIManager.from_json(os.path.join("rois", f"{args.profile}.json"))
    seat_roi = {sr.seat_index: sr.action_area for sr in mgr.rois.seat_regions
                if getattr(sr, "action_area", None) is not None and sr.action_area.width > 3}

    out = args.out or os.path.join("rois", f"action_refs_{args.profile}.json")
    refs = {}
    base_th, base_margin, base_grid, base_first = args.threshold, args.margin, args.grid, args.first_char
    if args.append and os.path.exists(out):  # 追加:载现有参考,新锚续上去(沿用现有 grid 保兼容)
        with open(out, encoding="utf-8") as f:
            ex = json.load(f)
        refs = ex.get("refs", {})
        base_th, base_margin = int(ex.get("match_threshold", args.threshold)), int(ex.get("margin", args.margin))
        base_grid = int(ex.get("grid", 8)); base_first = bool(ex.get("first_char", False))
        log.info(f"追加模式:载现有 {out}({', '.join(f'{w}:{len(h)}' for w, h in refs.items())}) grid={base_grid}")
    for tok in args.anchors.split(","):
        tok = tok.strip()
        if not tok:
            continue
        parts = tok.split(":")
        if len(parts) != 3:
            log.error(f"锚格式错: {tok!r}(应 frame:seat:label)"); sys.exit(2)
        fsub, seat_s, label = parts
        if label not in LABEL_TO_WORD:
            log.error(f"label {label!r} 未知(应 {list(LABEL_TO_WORD)})"); sys.exit(2)
        if not seat_s.isdigit():
            log.error(f"seat 须数字: {tok!r}"); sys.exit(2)
        seat = int(seat_s)
        fp = next((f for f in files if fsub in os.path.basename(f)), None)
        if fp is None:
            log.error(f"锚帧没找到: {fsub}"); sys.exit(2)
        a = seat_roi.get(seat)
        if a is None:
            log.error(f"座 {seat} 无 action_area"); sys.exit(2)
        frame = cv2.imread(fp)
        H, W = frame.shape[:2]
        if a.left < 0 or a.top < 0 or a.left + a.width > W or a.top + a.height > H:
            log.error(f"锚 {tok} 坐标越界"); sys.exit(2)
        crop = frame[a.top:a.top + a.height, a.left:a.left + a.width]
        h = text_shape_hash(crop, args.sat_th, args.val_th, base_grid, base_first)
        word = LABEL_TO_WORD[label]
        if not h:
            log.error(f"⚠️ 锚 {tok} 抠不出文字(空 hash)→ 调 --sat-th/--val-th 或换锚"); sys.exit(2)
        refs.setdefault(word, []).append(h)
        log.info(f"参考 [{label}→{word}] ← {os.path.basename(fp)} seat{seat}")

    payload = {"version": 1, "profile": args.profile,
               "sat_th": args.sat_th, "val_th": args.val_th, "grid": base_grid, "first_char": base_first,
               "match_threshold": base_th, "margin": base_margin, "refs": refs}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info(f"✅ 写 {out}: {{ {', '.join(f'{w}:{len(hs)}个' for w, hs in refs.items())} }} "
             f"阈值={args.threshold} margin={args.margin}")


if __name__ == "__main__":
    main()
