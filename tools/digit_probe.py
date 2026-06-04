r"""tools/digit_probe.py — 数字模板 OCR 探针(验 Option2,单 ROI,【零 EasyOCR】)。

模板从【你给的真值】建,验也对【真值】—— 不用 EasyOCR(它会把错标进模板 + 循环论证)。

流程:
  ① 采模板:--label "帧文件:已知值,…"(目标座该 ROI 的真实数值)→ 投影切割,
     若【格数==位数】按真值给每格打标签,存字形(首个干净实例)。几个值覆盖 0-9。
  ② 自检:对每个 label 帧用模板回读,断言 == 真值(模板能自洽复现)。
  ③ 泛化抽查:对录像里(非 label)帧模板读,dump 一批 (t, 文件, 读数) 供你眼裁。
  --dump-proj:打印 label 帧的列投影 + 切格,先确认切割(有缝)对不对。

⚠️ cv2,本机(无 cv2)未验证;纯切割/解析在 pipeline/digit_ocr.py(已单测)。
用法:
  python tools\digit_probe.py --session data\recordings\<ts> --seat 0 --field stack ^
      --label "f_000245.png:213, f_000300.png:1538, f_000600.png:46" [--dump-proj]
"""
import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "pipeline"))
import digit_ocr  # noqa: E402  纯核(已单测)


def _col_ink(gray, ink_th):
    import numpy as np
    return (gray > ink_th).astype(np.uint8).sum(axis=0).tolist()


def _match_topk(glyph, templates, k=3):
    """返回 top-k (字符, 分) —— 客观诊断用(看形近是否势均)。同 _match_char 的度量。"""
    import cv2
    import numpy as np
    scores = []
    for ch, tmpl in templates.items():
        g = cv2.resize(glyph, (tmpl.shape[1], tmpl.shape[0])).astype(np.float32)
        tf = tmpl.astype(np.float32)
        g -= g.mean(); tf -= tf.mean()
        denom = float(np.linalg.norm(g) * np.linalg.norm(tf)) or 1.0
        scores.append((ch, float((g * tf).sum() / denom)))
    scores.sort(key=lambda x: -x[1])
    return scores[:k]


def _match_char(glyph, templates, score_th):
    """灰度 + resize-到-模板 + 去均值归一相关。基线:18/20 位正确(仅 5↔6、8↔3 跨座混淆)。
    二值化版实测退步(细笔画毁于固定画布),已退回。提升读数走 per-seat 模板或 CNN。"""
    import cv2
    import numpy as np
    best, best_s = "?", -1.0
    for ch, tmpl in templates.items():
        g = cv2.resize(glyph, (tmpl.shape[1], tmpl.shape[0])).astype(np.float32)
        tf = tmpl.astype(np.float32)
        g -= g.mean(); tf -= tf.mean()
        denom = float(np.linalg.norm(g) * np.linalg.norm(tf)) or 1.0
        s = float((g * tf).sum() / denom)
        if s > best_s:
            best, best_s = ch, s
    return best if best_s >= score_th else "?"


def main():
    ap = argparse.ArgumentParser(description="数字模板 OCR 探针(零 EasyOCR)")
    ap.add_argument("--session", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--seat", type=int, default=0)
    ap.add_argument("--field", default="stack")
    ap.add_argument("--harvest", default="",
                    help='多座采模板:"帧=v0,v1,…,v7; 帧2=…"(一帧里 seats 0..N 的 stack 真值,同字体凑 0-9)')
    ap.add_argument("--harvest-file", default="",
                    help="从文件读真值块(每行 '帧=v0,..,v7',# 注释)——免跨终端粘长串被塞空格")
    ap.add_argument("--harvest-assist", type=int, default=0,
                    help="从全段 manifest 均匀挑 N 帧打文件名(供眼裁真值,跨整段保证数字多样性);打完即退")
    ap.add_argument("--decimate", type=int, default=10)
    ap.add_argument("--ink-th", type=int, default=150)
    ap.add_argument("--gap-th", type=int, default=0)
    ap.add_argument("--min-gap", type=int, default=2, help="缝<此列数→合并(治数字内细缝劈裂)")
    ap.add_argument("--min-cell-w", type=int, default=3, help="格<此px→丢(治1-2px噪点)")
    ap.add_argument("--max-merge-w", type=int, default=14, help="合并后宽>此→不并(防字间1px真缝误并两位)")
    ap.add_argument("--score-th", type=float, default=0.6)
    ap.add_argument("--mode-window", type=int, default=0,
                    help="每真值帧 ±W 帧密集读取众数 vs 真值(验时序中位能否吸收离群坏帧)")
    ap.add_argument("--diagnose", action="store_true",
                    help="客观数字诊断(列墨profile+raw/过滤cells+每格top-k分),不靠眼读像素")
    ap.add_argument("--save-crops", default="",
                    help="把各 harvest 帧的 --seat crop 存 PNG(放大6x+红绿线标切割边界)供眼诊断")
    ap.add_argument("--dump-proj", action="store_true")
    ap.add_argument("--no-normalize", dest="normalize", action="store_false",
                    help="关闭亮度归一(默认开:min-max拉满量程,已验消解发暗+5/6+跨座三害)")
    ap.set_defaults(normalize=True)
    ap.add_argument("--bootstrap", action="store_true",
                    help="pool 现有模板读全8座(起步),用户翻图只报错→纠错后建 per-seat 模板")
    ap.add_argument("--validate", type=int, default=0,
                    help="留出验证:随机挑 N 张非 harvest 帧,读全 8 座按 seat0-7 打印(供翻图核对)")
    ap.add_argument("--seed", type=int, default=0, help="--validate 随机种子(换数字重roll新帧)")
    args = ap.parse_args()

    # ── --harvest-assist:全段均匀挑 N 帧打文件名(纯 manifest,无 cv2),供眼裁真值 ──
    if args.harvest_assist > 0:
        mlines = (Path(args.session) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        frames = [json.loads(x) for x in mlines[1:] if x.strip()]
        n = args.harvest_assist
        if len(frames) <= n:
            picks = frames
        else:
            step = (len(frames) - 1) / (n - 1) if n > 1 else 0
            picks = [frames[round(i * step)] for i in range(n)]
        print(f"全段 {len(frames)} 帧,均匀挑 {len(picks)} 帧(开这些读 seat6/seat7 持有筹码):")
        for d in picks:
            print(f"  {d['file']}   t={d.get('t_mono', 0):.0f}s")
        print("\n读完按 '帧名=,,,,,,seat6,seat7' 报我(只填 6、7 座,前面 6 个逗号留空)。")
        return

    if not args.harvest and not args.harvest_file:
        print("需要 --harvest / --harvest-file / --harvest-assist N。"); return

    import cv2

    prof = json.loads((Path(_ROOT) / "rois" / f"{args.profile}.json").read_text(encoding="utf-8"))
    seat_rois = {s["seat_index"]: s[args.field] for s in prof["seats"] if s.get(args.field)}
    fdir = Path(args.session) / "frames"

    import numpy as np

    def gray_roi(fn, roi):
        img = cv2.imread(str(fdir / fn))
        if img is None:
            return None
        l, t, w, h = roi
        crop = img[t:t + h, l:l + w]
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        if args.normalize:  # 类2:亮度归一,治发暗帧漏墨。有内容才拉伸,防空crop放大噪声
            lo, hi = float(g.min()), float(g.max())
            if hi - lo >= 30:
                g = ((g.astype(np.float32) - lo) / (hi - lo) * 255).clip(0, 255).astype(np.uint8)
        return g

    # 解析真值块:来自 --harvest("帧=v0,…; 帧2=…")和/或 --harvest-file(每行一块,# 注释)
    # → [(帧, [v0,v1,…])](按 seat 序)。文件优先,免跨终端粘长串被塞空格。
    blocks = []
    if args.harvest_file:
        for line in Path(args.harvest_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                blocks.append(line)
    blocks += args.harvest.split(";")
    harvest = []
    for blk in blocks:
        blk = blk.strip()
        if not blk or "=" not in blk:
            continue
        fn, vals = blk.split("=", 1)
        harvest.append((fn.strip(), [v.strip() for v in vals.split(",")]))

    def cells_of(g):
        return digit_ocr.segment_cells(_col_ink(g, args.ink_th), args.gap_th,
                                       args.min_gap, args.min_cell_w, args.max_merge_w)

    # ── ① per-seat 采模板(每座只用自己的字形,跨帧累积凑齐数字表)──
    # seat_tpls[seat][char] = glyph;治跨座 5↔6、8↔3 渲染差(card_marker 同款解)。
    seat_tpls = {}
    for fn, vals in harvest:
        for si, val in enumerate(vals):
            if not val or si not in seat_rois:
                continue
            g = gray_roi(fn, seat_rois[si])
            if g is None:
                print(f"  ⚠️ 读不到 {fn}"); break
            cells = cells_of(g)
            if args.dump_proj:
                print(f"  [proj] {fn} seat{si} 真值'{val}' 切{len(cells)}格 {cells}")
            if len(cells) != len(val):
                print(f"  ⚠️ {fn} seat{si}: 切{len(cells)}格 ≠ 真值{len(val)}位 → 跳过(调 --ink-th/--gap-th)")
                continue
            tpls = seat_tpls.setdefault(si, {})
            for (x0, x1), ch in zip(cells, val):
                tpls.setdefault(ch, g[:, x0:x1 + 1].copy())
    print("\n采到模板(per-seat):")
    for si in sorted(seat_tpls):
        miss = sorted(set("0123456789") - set(seat_tpls[si]))
        print(f"  seat{si}: 有 {sorted(seat_tpls[si])}  缺 {miss}")

    def read_seat(fn, s):
        """用 seat s 自己的模板读该帧该座 → 数字串('空'=无墨/读空,'NA'=无ROI/无模板)。"""
        roi = seat_rois.get(s)
        tpl = seat_tpls.get(s)
        if roi is None or not tpl:
            return "NA"
        g = gray_roi(fn, roi)
        if g is None:
            return "NA"
        digits, _ = digit_ocr.parse_number(
            cells_of(g), lambda c: _match_char(g[:, c[0]:c[1] + 1], tpl, args.score_th))
        return digits or "空"

    # ── bootstrap:pool 现有模板读全 8 座(起步用,用户翻图只报错)→ 纠错后建 per-seat ──
    if args.bootstrap:
        pool = {}
        for s in sorted(seat_tpls):  # seat6 全 0-9 → 先 pool 进来
            for ch, gl in seat_tpls[s].items():
                pool.setdefault(ch, gl)
        print(f"\n=== bootstrap 读全8座(pooled模板{sorted(pool)};跨座+normalize起步,必有错)===")
        for fn, _ in harvest:
            cols = []
            for s in range(8):
                roi = seat_rois.get(s)
                if roi is None:
                    cols.append(f"s{s}=NA"); continue
                g = gray_roi(fn, roi)
                if g is None:
                    cols.append(f"s{s}=NA"); continue
                digits, _ = digit_ocr.parse_number(
                    cells_of(g), lambda c: _match_char(g[:, c[0]:c[1] + 1], pool, args.score_th))
                cols.append(f"s{s}={digits or '空'}")
            print(f"  {fn}  " + "  ".join(cols))
        print("\n翻这12帧核对,**只把读错的**告诉我(帧+座+正确值),我建 per-seat 模板。")
        return

    # ── 留出验证:pool 模板读【随机非 harvest 帧】全 8 座(真·held-out + 跨座)──
    if args.validate > 0:
        import random
        pool_tpl = {}
        for s in sorted(seat_tpls):  # 一套 pool 通吃(bootstrap 已证跨座+normalize 可行)
            for ch, gl in seat_tpls[s].items():
                pool_tpl.setdefault(ch, gl)
        mlines = (Path(args.session) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        frames_all = [json.loads(x) for x in mlines[1:] if x.strip()]
        hv = {fn for fn, _ in harvest}
        cand = [d for d in frames_all if d["file"] not in hv]
        picks = random.Random(args.seed).sample(cand, min(args.validate, len(cand)))
        picks.sort(key=lambda d: d["file"])
        print(f"\n=== 留出验证(seed={args.seed},{len(picks)} 随机非harvest帧×全8座,pool模板{sorted(pool_tpl)})===")
        for d in picks:
            cols = []
            for s in range(8):
                roi = seat_rois.get(s)
                if roi is None:
                    cols.append(f"s{s}=NA"); continue
                g = gray_roi(d["file"], roi)
                if g is None:
                    cols.append(f"s{s}=NA"); continue
                digits, _ = digit_ocr.parse_number(
                    cells_of(g), lambda c: _match_char(g[:, c[0]:c[1] + 1], pool_tpl, args.score_th))
                cols.append(f"s{s}={digits or '空'}")
            print(f"  {d['file']}  " + "  ".join(cols))
        print("\n翻这几帧核对:工具读 vs 你眼看。不一致 → 两边都重查(人/工具都会幻觉)。")
        return

    templates = seat_tpls.get(args.seat, {})
    if not templates:
        print(f"\nseat{args.seat} 没采到模板 —— 给含该座的 --harvest 帧,且 --dump-proj 调对切割(格数==位数)。"); return
    print(f"\n→ 用 seat{args.seat} 自己的模板读 seat{args.seat}(纯 per-seat,无跨座)")

    def tmpl_read(g):
        digits, _ = digit_ocr.parse_number(
            cells_of(g), lambda c: _match_char(g[:, c[0]:c[1] + 1], templates, args.score_th))
        return digits

    # ── 客观数字诊断(不靠眼读):列墨profile + raw/过滤cells + 每格top-k 分 ──
    if args.diagnose:
        print(f"\n=== 客观诊断 seat{args.seat}(profile=确定性渲染的列墨量,非像素眼读)===")
        for fn, vals in harvest:
            if args.seat >= len(vals) or not vals[args.seat]:
                continue
            g = gray_roi(fn, seat_rois[args.seat])
            if g is None:
                continue
            ink = _col_ink(g, args.ink_th)
            mx = max(ink) or 1
            prof = "".join("_" if v == 0 else "." if v <= mx / 3 else ":" if v <= 2 * mx / 3 else "#"
                           for v in ink)  # _=无墨(可见,防粘贴吞前导空格)
            raw = digit_ocr.segment_cells(ink, args.gap_th, 0, 0)        # 未过滤纯墨段
            cells = cells_of(g)                                          # 当前参数过滤后
            print(f"\n{fn} 真值'{vals[args.seat]}' (宽{len(ink)}px ink-th={args.ink_th}):")
            print(f"  |{prof}|")
            print(f"  raw runs(未过滤,(x0,x1,宽)): {[(a, b, b - a + 1) for a, b in raw]}")
            print(f"  cells(min_gap={args.min_gap} min_cell_w={args.min_cell_w}): "
                  f"{[(a, b, b - a + 1) for a, b in cells]}")
            for j, (x0, x1) in enumerate(cells):
                top = _match_topk(g[:, x0:x1 + 1], templates, 3)
                print(f"    cell{j}({x0}-{x1}): " + "  ".join(f"{c}={s:.2f}" for c, s in top))
        return

    # ── 存 crop 供眼诊断:放大 6x + 红(左界)绿(右界)线标切割,我直接看像素 ──
    if args.save_crops:
        if args.seat not in seat_rois:
            print(f"seat{args.seat} 无 {args.field} ROI。"); return
        outdir = Path(args.save_crops)
        outdir.mkdir(parents=True, exist_ok=True)
        print(f"\n=== 存 seat{args.seat} crop(放大6x+切割线)到 {outdir} ===")
        for fn, vals in harvest:
            g = gray_roi(fn, seat_rois[args.seat])
            if g is None:
                continue
            cells = cells_of(g)
            big = cv2.resize(g, (g.shape[1] * 6, g.shape[0] * 6), interpolation=cv2.INTER_NEAREST)
            vis = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)
            for (x0, x1) in cells:
                cv2.line(vis, (x0 * 6, 0), (x0 * 6, vis.shape[0] - 1), (0, 0, 255), 1)
                cv2.line(vis, ((x1 + 1) * 6, 0), ((x1 + 1) * 6, vis.shape[0] - 1), (0, 255, 0), 1)
            val = vals[args.seat] if args.seat < len(vals) else ""
            out = outdir / f"{Path(fn).stem}_s{args.seat}_t{val}_切{len(cells)}.png"
            cv2.imwrite(str(out), vis)
            print(f"  {out.name}")
        print("\n把读错那几张(4021/3151/3511/803…)贴给我,我直接看像素诊断。")
        return

    # ── ②.5 时序中位检查:每真值帧 ±W 帧密集读取众数 vs 真值(验离群坏帧能否被吸收)──
    if args.mode_window > 0:
        from collections import Counter
        mlines = (Path(args.session) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        frames_all = [json.loads(x) for x in mlines[1:] if x.strip()]
        idx_of = {d["file"]: i for i, d in enumerate(frames_all)}
        W = args.mode_window
        print(f"\n=== ②.5 时序中位(seat{args.seat},每真值帧 ±{W} 帧取众数 vs 真值)===")
        ok = tot = 0
        for fn, vals in harvest:
            if args.seat >= len(vals) or not vals[args.seat]:
                continue
            truth = vals[args.seat]
            i = idx_of.get(fn)
            if i is None:
                print(f"  {fn}: 不在 manifest"); continue
            reads = []
            for d in frames_all[max(0, i - W): i + W + 1]:
                g = gray_roi(d["file"], seat_rois[args.seat])
                if g is None:
                    continue
                r = tmpl_read(g)
                if r:
                    reads.append(r)
            if not reads:
                print(f"  {fn} 真值{truth}: 窗口内无读数"); continue
            mode, cnt = Counter(reads).most_common(1)[0]
            frame_ok = sum(1 for r in reads if r == truth)
            tot += 1; ok += (mode == truth)
            print(f"  {fn} 真值{truth}: 众数'{mode}'({cnt}/{len(reads)}) | per帧对{frame_ok}/{len(reads)}  "
                  f"{'✅' if mode == truth else '❌'}")
        print(f"\n时序中位 per-值准确率: {ok}/{tot}(对比 per-帧 ~75%)")
        return

    # ── ② 自检:harvest 帧里 --seat 那座回读 == 真值 ──
    print(f"\n=== ② 自检(harvest 帧 seat{args.seat} 回读 vs 真值)===")
    for fn, vals in harvest:
        if args.seat >= len(vals) or args.seat not in seat_rois:
            continue
        g = gray_roi(fn, seat_rois[args.seat])
        if g is None:
            continue
        r, val = tmpl_read(g), vals[args.seat]
        print(f"  {fn} seat{args.seat}: 模板读'{r}' vs 真值'{val}'  {'✅' if r == val else '❌'}")

    # ── ③ 泛化抽查:--seat 这座跨帧模板读 ──
    manifest = (Path(args.session) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    frames = [json.loads(x) for x in manifest[1:] if x.strip()]
    hv_files = {fn for fn, _ in harvest}
    print(f"\n=== ③ 泛化抽查(seat{args.seat},非 harvest 帧,模板读,眼裁)===")
    shown = 0
    for k, d in enumerate(frames):
        if k % args.decimate or d["file"] in hv_files or args.seat not in seat_rois:
            continue
        g = gray_roi(d["file"], seat_rois[args.seat])
        if g is None:
            continue
        r = tmpl_read(g)
        if r:
            print(f"  t{d.get('t_mono',0):.1f} {d['file']}: '{r}'")
            shown += 1
        if shown >= 25:
            break
    print("\n判读:② 全 ✅ = 模板自洽;③ 开帧图眼裁读数对不对(尤其小额)。准 → 模板替 EasyOCR 成立。")


if __name__ == "__main__":
    main()
