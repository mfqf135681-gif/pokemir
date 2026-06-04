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


def _corr(glyph, tmpl):
    """去均值归一相关(glyph resize 到 tmpl 尺寸)。"""
    import cv2
    import numpy as np
    g = cv2.resize(glyph, (tmpl.shape[1], tmpl.shape[0])).astype(np.float32)
    tf = tmpl.astype(np.float32)
    g -= g.mean(); tf -= tf.mean()
    return float((g * tf).sum() / (float(np.linalg.norm(g) * np.linalg.norm(tf)) or 1.0))


def _match_char(glyph, templates, score_th):
    """单样本:templates={char:glyph}。"""
    best, best_s = "?", -1.0
    for ch, tmpl in templates.items():
        s = _corr(glyph, tmpl)
        if s > best_s:
            best, best_s = ch, s
    return best if best_s >= score_th else "?"


def _match_char_multi(glyph, exemplars, score_th):
    """多样本:exemplars={char:[glyph,...]}。每字取其所有样本最高分,选最高字。
    治跨座小字 5↔6 类:留各座的 5,某座的 5 字形匹配到自己那个 5 样本 → 胜过 6。"""
    best, best_s = "?", -1.0
    for ch, gls in exemplars.items():
        s = max(_corr(glyph, t) for t in gls)
        if s > best_s:
            best, best_s = ch, s
    return best if best_s >= score_th else "?"


def main():
    ap = argparse.ArgumentParser(description="数字模板 OCR 探针(零 EasyOCR)")
    ap.add_argument("--session", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--seat", type=int, default=0)
    ap.add_argument("--field", default="stack")
    ap.add_argument("--read-field", default="",
                    help="模板从 --field 建,但 scan/bootstrap/validate 实际读此区(测 stack 模板跨区读 amount)")
    ap.add_argument("--harvest", default="",
                    help='多座采模板:"帧=v0,v1,…,v7; 帧2=…"(一帧里 seats 0..N 的 stack 真值,同字体凑 0-9)')
    ap.add_argument("--harvest-file", default="",
                    help="从文件读真值块(每行 '帧=v0,..,v7',# 注释)——免跨终端粘长串被塞空格")
    ap.add_argument("--harvest-session", default="",
                    help="模板采自此录像(默认=--session)。设它=用A录像模板读B录像→跨录像迁移测试")
    ap.add_argument("--harvest-src", action="append", default=[],
                    help="多源采模板 '录像路径|真值文件'(可多次):各源帧从各自录像读,exemplar汇入同pool→密化/多录像合并")
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
    ap.add_argument("--binarize", action="store_true",
                    help="硬二值化:>bin_th纯白/否则纯黑,杀抗锯齿过渡块+纹路(都中灰)→只留数字白核")
    ap.add_argument("--bin-th", type=int, default=200, help="--binarize 阈值(默认200,只留近纯白)")
    ap.add_argument("--bg-frame", default="",
                    help="静态背景参考帧(无内容帧):每ROI减背景纹理→前景凸出(纹理须静态)。给绝对/相对路径")
    ap.add_argument("--bootstrap", action="store_true",
                    help="pool 现有模板读全8座(起步),用户翻图只报错→纠错后建 per-seat 模板")
    ap.add_argument("--validate", type=int, default=0,
                    help="留出验证:随机挑 N 张非 harvest 帧,读全 8 座按 seat0-7 打印(供翻图核对)")
    ap.add_argument("--seed", type=int, default=0, help="--validate 随机种子(换数字重roll新帧)")
    ap.add_argument("--verify", action="store_true",
                    help="交互核对:配 --scan/--validate,每读弹放大crop+console确认(回车对/输正确值/s遮挡/q退)")
    ap.add_argument("--verify-out", default="",
                    help="--verify 把已核值(确认+纠正)写入此真值文件(帧=v0..v7),零手填")
    ap.add_argument("--check", default="",
                    help="拿已核真值文件当真相跑工具 diff,列错项+准确率(零重验,可跨录像)")
    ap.add_argument("--scan", type=int, default=0,
                    help="扫每N帧,pool模板读 read-field 真下注(过滤空/ante10),报读值+格数供眼核")
    ap.add_argument("--min-hit-cells", type=int, default=4,
                    help="--scan 只报切格≥此的座(图标+3位数=真大注,滤掉ante;默认4)")
    ap.add_argument("--only-seats", default="",
                    help="scan/bootstrap/validate 只处理这些座(如 '0')——别座已验时省事重跑单座")
    ap.add_argument("--icon-prefix", action="store_true",
                    help="采集时若切格>位数,丢多余格(筹码图标)对齐真值 — 下注区amount用")
    ap.add_argument("--icon-right-seats", default="",
                    help="这些座筹码图标在数字【右】侧(丢尾格);其余在左(丢首格)。如 '5,6,7'")
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

    if not (args.harvest or args.harvest_file or args.harvest_src or args.scan):
        print("需要 --harvest / --harvest-file / --harvest-src / --harvest-assist N / --scan N。"); return

    import cv2

    prof = json.loads((Path(_ROOT) / "rois" / f"{args.profile}.json").read_text(encoding="utf-8"))

    def _rois_for(field):
        """座级字段 → {seat:roi};顶层表级 ROI(如 pot_size)→ {0:roi} 当伪座0。"""
        if not field:
            return {}
        sr = {s["seat_index"]: s[field] for s in prof["seats"] if s.get(field)}
        if not sr and isinstance(prof.get(field), list):  # 表级单 ROI(底池等)
            sr = {0: prof[field]}
        return sr

    seat_rois = _rois_for(args.field)
    # 模板从 --field 建,scan/bootstrap/validate 实际读 --read-field(测跨区,如 stack模板读amount/pot)
    read_rois = _rois_for(args.read_field) if args.read_field else seat_rois
    icon_right = {int(x) for x in args.icon_right_seats.split(",") if x.strip()}
    seats_iter = [int(x) for x in args.only_seats.split(",") if x.strip()] or list(range(8))
    fdir = Path(args.session) / "frames"                       # 读取(scan/validate)的目标录像
    hdir = Path(args.harvest_session) / "frames" if args.harvest_session else fdir  # 采模板的录像
    if args.harvest_session:
        print(f"[跨录像] 模板采自 {args.harvest_session},读取打到 {args.session}")

    import numpy as np

    # 静态背景纹理参考(--bg-frame):整帧灰度,扣除时取同 ROI。治静态纹理污染(实测纹理静态)。
    bg_gray = None
    if args.bg_frame:
        _bg = cv2.imread(args.bg_frame)
        if _bg is None:
            print(f"  ⚠️ 读不到 bg-frame {args.bg_frame}")
        else:
            bg_gray = cv2.cvtColor(_bg, cv2.COLOR_BGR2GRAY)
            print(f"[bg扣除] 背景参考 {args.bg_frame}")

    def gray_roi(fn, roi, fdir=fdir):
        img = cv2.imread(str(fdir / fn))
        if img is None:
            return None
        l, t, w, h = roi
        crop = img[t:t + h, l:l + w]
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        if bg_gray is not None:  # 减静态背景纹理 → 纹理归0、前景(数字)凸出
            g = cv2.absdiff(g, bg_gray[t:t + h, l:l + w])
        if args.binarize:  # 硬二值:>bin_th=纯白(数字核),否则纯黑→杀抗锯齿过渡块+纹路(都中灰)
            return ((g > args.bin_th).astype(np.uint8) * 255)
        if args.normalize:  # 亮度归一:p2–p98 拉伸(杂散纯黑/白点不再主导 min-max→治暗黄字卡阈值)
            lo, hi = float(np.percentile(g, 2)), float(np.percentile(g, 98))
            if hi - lo >= 30:
                g = ((g.astype(np.float32) - lo) / (hi - lo) * 255).clip(0, 255).astype(np.uint8)
        return g

    # 解析真值块:来自 --harvest("帧=v0,…; 帧2=…")和/或 --harvest-file(每行一块,# 注释)
    # → [(帧, [v0,v1,…])](按 seat 序)。文件优先,免跨终端粘长串被塞空格。
    def _parse_blocks(raw_lines):
        out = []
        for blk in raw_lines:
            blk = blk.strip()
            if not blk or blk.startswith("#") or "=" not in blk:
                continue
            fn, vals = blk.split("=", 1)
            out.append((fn.strip(), [v.strip() for v in vals.split(",")]))
        return out

    # 采集源:--harvest-src "录像|真值文件"(可多次,各源帧从各自session读)→ 密化/合并多录像 pool。
    # 缺省单源:--harvest-file/--harvest 的块从 hdir(--harvest-session 或 --session)读。
    sources = []  # [(frames_dir, [(fn,vals),...])]
    for src in args.harvest_src:
        sess, tf = src.split("|", 1)
        blks = _parse_blocks(Path(tf.strip()).read_text(encoding="utf-8").splitlines())
        sources.append((Path(sess.strip()) / "frames", blks))
    if not sources:
        raw = []
        if args.harvest_file:
            raw += Path(args.harvest_file).read_text(encoding="utf-8").splitlines()
        raw += args.harvest.split(";")
        sources.append((hdir, _parse_blocks(raw)))
    harvest = [b for _, blks in sources for b in blks]  # 汇总(供 validate 排除/bootstrap/②引用)

    def cells_of(g):
        return digit_ocr.segment_cells(_col_ink(g, args.ink_th), args.gap_th,
                                       args.min_gap, args.min_cell_w, args.max_merge_w)

    # ── ① per-seat 采模板(每座只用自己的字形,跨帧累积凑齐数字表)──
    # seat_tpls[seat][char] = glyph;治跨座 5↔6、8↔3 渲染差(card_marker 同款解)。
    seat_tpls = {}
    for fdir_src, blks in sources:
      for fn, vals in blks:
        for si, val in enumerate(vals):
            if not val or not val.isdigit() or si not in seat_rois:
                continue  # 跳过空 + 非数字(遮挡标签"公告污染"等)
            g = gray_roi(fn, seat_rois[si], fdir_src)  # 各源帧从各自 session 读
            if g is None:
                print(f"  ⚠️ 读不到 {fn}"); break
            cells = cells_of(g)
            use = cells
            if args.icon_prefix and len(cells) > len(val):
                # 筹码图标在数字旁:icon_right_seats 座图标在右(取左侧len格),其余在左(取右侧len格)
                use = cells[:len(val)] if si in icon_right else cells[-len(val):]
            if args.dump_proj:
                print(f"  [proj] {fn} seat{si} 真值'{val}' 切{len(cells)}格 用{len(use)}格 {cells}")
            if len(use) != len(val):
                print(f"  ⚠️ {fn} seat{si}: 切{len(cells)}格→用{len(use)} ≠ 真值{len(val)}位 → 跳过")
                continue
            tpls = seat_tpls.setdefault(si, {})
            for (x0, x1), ch in zip(use, val):
                tpls.setdefault(ch, g[:, x0:x1 + 1].copy())
    print("\n采到模板(per-seat):")
    for si in sorted(seat_tpls):
        miss = sorted(set("0123456789") - set(seat_tpls[si]))
        print(f"  seat{si}: 有 {sorted(seat_tpls[si])}  缺 {miss}")

    def _pool():
        """多样本 pool:{char:[各座该字的样本]} → 配 _match_char_multi 治跨座小字混淆。"""
        p = {}
        for s in sorted(seat_tpls):
            for ch, gl in seat_tpls[s].items():
                p.setdefault(ch, []).append(gl)
        return p

    # ── 交互核对(--verify):弹放大crop + console 确认 → 直接产出已核真值,杀手动翻图+转录错 ──
    vacc = {"ok": 0, "bad": 0, "skip": 0, "fixed": {}, "corr": []}  # fixed:{frame:{seat:已核值}} corr:纠正明细

    def _verify_one(fn, s, g, read):
        """弹 crop + 提示。返回 True 继续 / False 退出(q)。回车=对/数字=纠正/s=遮挡/q=退。"""
        big = cv2.resize(g, (g.shape[1] * 8, g.shape[0] * 8), interpolation=cv2.INTER_NEAREST)
        cv2.imshow("verify  (看图,到console敲键)", big)
        cv2.waitKey(60)  # 渲染窗口
        ans = input(f"  {fn} s{s} 读='{read}' ? [回车=对/输正确值/s=遮挡/q=退]: ").strip()
        if ans == "q":
            return False
        if ans == "s":
            vacc["skip"] += 1
        elif ans == "":
            vacc["ok"] += 1
            vacc["fixed"].setdefault(fn, {})[s] = read
        else:
            vacc["bad"] += 1
            vacc["fixed"].setdefault(fn, {})[s] = ans
            vacc["corr"].append((fn, s, read, ans))  # (帧,座,工具读,正确值)
        return True

    def _verify_done():
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
        tot = vacc["ok"] + vacc["bad"]
        acc = f"{vacc['ok']}/{tot}" if tot else "0/0"
        summary = f"对{vacc['ok']} 错{vacc['bad']} 遮挡{vacc['skip']} → 准确率 {acc}"
        print(f"\n核对完: {summary}")
        for fn, s, read, ans in vacc["corr"]:
            print(f"  纠正 {fn} s{s}: 工具读'{read}' → 正确'{ans}'")
        if args.verify_out and vacc["fixed"]:
            tgt = args.read_field or args.field
            hdr = [f"# 交互核对 {tgt} @ {args.session}",
                   f"# 准确率 {summary}"]
            for fn, s, read, ans in vacc["corr"]:
                hdr.append(f"#   纠正 {fn} s{s}: 工具读'{read}' → 正确'{ans}'")
            lines = []
            for fn in sorted(vacc["fixed"]):
                row = ["" for _ in range(8)]
                for s, v in vacc["fixed"][fn].items():
                    row[s] = v
                lines.append(f"{fn}={','.join(row)}")
            Path(args.verify_out).write_text(
                "\n".join(hdr) + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
            print(f"  已核真值写入 {args.verify_out}({len(lines)} 帧;含准确率+纠正明细)")

    # ── 客观诊断:列墨profile + 灰度min/max(查反相/暗字)+ cells。吃 --check 文件当目标 ──
    if args.diagnose:
        entries = []
        if args.check:
            for line in Path(args.check).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    fn, vals = line.split("=", 1)
                    for s, t in enumerate([x.strip() for x in vals.split(",")]):
                        if t and t.isdigit():
                            entries.append((fn.strip(), s, t))
        else:
            for fn, vals in harvest:
                if args.seat < len(vals) and vals[args.seat]:
                    entries.append((fn, args.seat, vals[args.seat]))
        tgt = args.read_field or args.field
        print(f"\n=== 客观诊断 {tgt}(列墨profile + 灰度min/max查反相/暗字 + cells)===")
        for fn, s, truth in entries:
            roi = read_rois.get(s)
            if roi is None:
                continue
            g = gray_roi(fn, roi)
            if g is None:
                continue
            gmin, gmax, gmean = int(g.min()), int(g.max()), int(g.mean())
            ink = _col_ink(g, args.ink_th)
            mx = max(ink) or 1
            prof = "".join("_" if v == 0 else "." if v <= mx / 3 else ":" if v <= 2 * mx / 3 else "#"
                           for v in ink)
            cells = cells_of(g)
            bright = float((g > args.ink_th).mean())
            print(f"\n{fn} s{s} 真值'{truth}' (灰度min{gmin}/max{gmax}/mean{gmean} >th占比{bright:.0%} 切{len(cells)}格):")
            print(f"  |{prof}|")
        return

    # ── --check:拿已核真值文件当真相,跑工具 diff,列出错项(零重验,可跨录像)──
    if args.check:
        pool = _pool()
        ok = bad = 0
        misses = []
        for line in Path(args.check).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            fn, vals = line.split("=", 1)
            for s, truth in enumerate([x.strip() for x in vals.split(",")]):
                if not truth or not truth.isdigit():   # 跳过空 + 非数字(遮挡标签等)
                    continue
                roi = read_rois.get(s)
                if roi is None:
                    continue
                g = gray_roi(fn.strip(), roi)
                if g is None:
                    continue
                r, _ = digit_ocr.parse_number(
                    cells_of(g), lambda c: _match_char_multi(g[:, c[0]:c[1] + 1], pool, args.score_th))
                if r == truth:
                    ok += 1
                else:
                    bad += 1
                    misses.append((fn.strip(), s, truth, r))
        tot = ok + bad
        print(f"\n=== --check {args.check}: 对{ok} 错{bad} → 准确率 {ok}/{tot} ===")
        for fn, s, t, r in misses:
            print(f"  ✗ {fn} s{s}: 真值'{t}' 工具读'{r}'")
        return

    # ── 扫真下注:pool 模板读 read-field,过滤掉空+ante('10')+短格,只留真大注供眼核 ──
    if args.scan > 0:
        pool = _pool()
        mlines = (Path(args.session) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        frames_all = [json.loads(x) for x in mlines[1:] if x.strip()]
        tgt = args.read_field or args.field
        skip = {"", "10"}  # 空 + ante;只看真下注
        print(f"\n=== 扫 {tgt} 真下注(每{args.scan}帧,≥{args.min_hit_cells}格,过滤空/10,去重连续同值)===")
        hits = 0
        last = {}  # seat -> 上次显示值(去重:同座连续相同只报一次,值变了才再报)
        quit_v = False
        for k, d in enumerate(frames_all):
            if k % args.scan or quit_v:
                continue
            seats_hit = []
            for s in seats_iter:
                roi = read_rois.get(s)
                if roi is None:
                    continue
                g = gray_roi(d["file"], roi)
                cur, rval = "", ""
                if g is not None:
                    c = cells_of(g)
                    if len(c) >= args.min_hit_cells:
                        r, _ = digit_ocr.parse_number(
                            c, lambda cc: _match_char_multi(g[:, cc[0]:cc[1] + 1], pool, args.score_th))
                        if r not in skip:
                            cur, rval = f"{r or '空'}({len(c)})", r
                if cur and cur != last.get(s):
                    if args.verify:
                        if not _verify_one(d["file"], s, g, rval):
                            quit_v = True; break
                    else:
                        seats_hit.append(f"s{s}={cur}")
                    hits += 1
                last[s] = cur
            if seats_hit:
                print(f"  {d['file']} t={d.get('t_mono', 0):.0f}: {' '.join(seats_hit)}")
        if args.verify:
            _verify_done()
        else:
            print(f"\n不同的真下注 {hits} 个(已去重连续同值;格式 sX=工具读值(切格数))。翻图核对——尤其图标污染。")
        return

    # ── bootstrap:pool 现有模板读全 8 座(起步用,用户翻图只报错)→ 纠错后建 per-seat ──
    if args.bootstrap:
        pool = _pool()
        tgt = args.read_field or args.field
        print(f"\n=== bootstrap 读全8座 {tgt}(pooled模板{sorted(pool)};跨座+normalize起步,必有错)===")
        for fn, _ in harvest:
            cols = []
            for s in seats_iter:
                roi = read_rois.get(s)
                if roi is None:
                    cols.append(f"s{s}=NA"); continue
                g = gray_roi(fn, roi)
                if g is None:
                    cols.append(f"s{s}=NA"); continue
                digits, _ = digit_ocr.parse_number(
                    cells_of(g), lambda c: _match_char_multi(g[:, c[0]:c[1] + 1], pool, args.score_th))
                cols.append(f"s{s}={digits or '空'}")
            print(f"  {fn}  " + "  ".join(cols))
        print("\n翻这12帧核对,**只把读错的**告诉我(帧+座+正确值),我建 per-seat 模板。")
        return

    # ── 留出验证:pool 模板读【随机非 harvest 帧】全 8 座(真·held-out + 跨座)──
    if args.validate > 0:
        import random
        pool_tpl = _pool()  # 多样本 pool(跨座小字混淆靠多样本治)
        mlines = (Path(args.session) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        frames_all = [json.loads(x) for x in mlines[1:] if x.strip()]
        hv = {fn for fn, _ in harvest}
        cand = [d for d in frames_all if d["file"] not in hv]
        picks = random.Random(args.seed).sample(cand, min(args.validate, len(cand)))
        picks.sort(key=lambda d: d["file"])
        tgt = args.read_field or args.field
        print(f"\n=== 留出验证(seed={args.seed},{len(picks)} 随机非harvest帧×全8座 {tgt},pool模板{sorted(pool_tpl)})===")
        quit_v = False
        for d in picks:
            if quit_v:
                break
            cols = []
            for s in seats_iter:
                roi = read_rois.get(s)
                if roi is None:
                    cols.append(f"s{s}=NA"); continue
                g = gray_roi(d["file"], roi)
                if g is None:
                    cols.append(f"s{s}=NA"); continue
                digits, _ = digit_ocr.parse_number(
                    cells_of(g), lambda c: _match_char_multi(g[:, c[0]:c[1] + 1], pool_tpl, args.score_th))
                if args.verify:
                    if digits:  # 只核非空读数
                        if not _verify_one(d["file"], s, g, digits):
                            quit_v = True; break
                else:
                    cols.append(f"s{s}={digits or '空'}")
            if not args.verify:
                print(f"  {d['file']}  " + "  ".join(cols))
        if args.verify:
            _verify_done()
        else:
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
