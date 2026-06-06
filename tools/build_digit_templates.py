r"""tools/build_digit_templates.py — 从真值文件采 stack 数字模板 → 存 JSON(供管线兜底读)。

把 digit_probe 的"采模板"沉淀成一份【可加载的模板文件】,让 `read_stack` 能在 EasyOCR
读空(孤立0等)时兜底用配方读。Win-only(cv2)。

流程:① 读真值块(帧=v0,..,v7,只填有数字的座)→ 各座 stack ROI 灰度归一+列墨切格;
       ② 若切格数==真值位数,glyph 按位打标签汇入共享 pool {char:[glyphs]};
       ③ 存 JSON;④ 自检:回读每个 harvest 帧的座,报准确率(模板能否自洽复现)。

⚠️ 复制粘贴友好:全短参数,真值走 --harvest-file(文件,不在命令行粘长串)。

用法(Win,先 git pull):
  # stack(大白字,无图标)→ 各区独立文件名带 _stack
  .\.venv\Scripts\python.exe tools\build_digit_templates.py ^
      --session data\recordings\20260603_121925 ^
      --harvest-file tools\truth_digit_121925.txt ^
      --out rois\digit_templates_party_poker_8_stack.json

  # amount(下注区,小粗字+筹码图标)→ --field amount --icon-prefix(右侧图标座列 --icon-right-seats)
  .\.venv\Scripts\python.exe tools\build_digit_templates.py ^
      --field amount --icon-prefix --icon-right-seats 5,6,7 ^
      --session data\recordings\<amount录像> ^
      --harvest-file tools\truth_amount_<ts>.txt ^
      --out rois\digit_templates_party_poker_8_amount.json
"""
import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "pipeline"))
import digit_ocr  # noqa: E402
import digit_reader  # noqa: E402


def _parse_blocks(lines):
    out = []
    for blk in lines:
        blk = blk.strip()
        if not blk or blk.startswith("#") or "=" not in blk:
            continue
        fn, vals = blk.split("=", 1)
        out.append((fn.strip(), [v.strip() for v in vals.split(",")]))
    return out


def main():
    ap = argparse.ArgumentParser(description="采 stack 数字模板 → JSON(管线兜底读用)")
    ap.add_argument("--session", required=True, help=r"采模板的录像目录")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--field", default="stack")
    ap.add_argument("--harvest-file", action="append", default=[],
                    help="真值文件(帧=v0,..,v7;可多次=多录像/多源密化)。多源时与 --session 列表一一对应")
    ap.add_argument("--harvest-session", action="append", default=[],
                    help="多源:各真值文件对应的录像(与 --harvest-file 同序);省则全用 --session")
    ap.add_argument("--out", required=True, help="模板 JSON 输出路径")
    ap.add_argument("--validate-file", default="",
                    help="留出验证真值文件(不参与采样,只用建好的模板回读报准度)。配 --validate-session。")
    ap.add_argument("--validate-session", default="",
                    help="留出验证的录像目录(与 --validate-file 配对)。")
    ap.add_argument("--ink-th", type=int, default=digit_reader.INK_TH)
    # amount 下注区图标处理(忠实复制 digit_probe.py:261-263 实证逻辑;座有左右之分)
    ap.add_argument("--icon-prefix", action="store_true",
                    help="amount 下注区:crop 含筹码图标 → 切格>真值位数时丢多余格对齐。"
                         "stack/pot 无图标,不要开。")
    ap.add_argument("--icon-right-seats", default="",
                    help="这些座筹码图标在数字【右】侧(取左 len 格);其余在左(取右 len 格)。如 '5,6,7'。"
                         "镜像 UI 渲染(同 player-id phash 左右镜像现象)——设错边把图标采成数字污染池。")
    args = ap.parse_args()

    import cv2

    icon_right = {int(x) for x in args.icon_right_seats.split(",") if x.strip()}
    prof = json.loads((Path(_ROOT) / "rois" / f"{args.profile}.json").read_text(encoding="utf-8"))
    seat_rois = {s["seat_index"]: s[args.field] for s in prof["seats"] if s.get(args.field)}

    # 源列表:(录像frames目录, 真值块)
    sessions = args.harvest_session or [args.session] * len(args.harvest_file)
    if len(sessions) < len(args.harvest_file):
        sessions += [args.session] * (len(args.harvest_file) - len(sessions))
    sources = []
    for tf, sess in zip(args.harvest_file, sessions):
        blks = _parse_blocks(Path(tf).read_text(encoding="utf-8").splitlines())
        sources.append((Path(sess) / "frames", blks, tf))

    def gray_roi(fdir, fn, roi):
        img = cv2.imread(str(fdir / fn))
        if img is None:
            return None
        l, t, w, h = roi
        return digit_reader._gray_normalize(img[t:t + h, l:l + w])

    def cells_of(g):
        return digit_ocr.segment_cells(digit_reader._col_ink(g, args.ink_th),
                                       digit_reader.GAP_TH, digit_reader.MIN_GAP,
                                       digit_reader.MIN_CELL_W, digit_reader.MAX_MERGE_W)

    pool = {}            # {char: [glyph,...]} 共享 pool
    harvested = []       # 供自检:(fdir, fn, seat, truth)
    kept = skipped = 0
    for fdir, blks, tf in sources:
        for fn, vals in blks:
            for si, val in enumerate(vals):
                if not val or not val.isdigit() or si not in seat_rois:
                    continue
                g = gray_roi(fdir, fn, seat_rois[si])
                if g is None:
                    print(f"  ⚠️ 读不到 {fdir/fn}"); continue
                cells = cells_of(g)
                # amount:筹码图标在数字旁 → 切格>真值位数时丢图标格(忠实复制 digit_probe.py:261-263)。
                # icon_right 座图标在右(取左 len 格),其余在左(取右 len 格)。设错边=污染池。
                if args.icon_prefix and len(cells) > len(val):
                    cells = cells[:len(val)] if si in icon_right else cells[-len(val):]
                if len(cells) != len(val):
                    print(f"  跳过 {fn} s{si} 真值'{val}'({len(val)}位)→切{len(cells)}格(不齐)")
                    skipped += 1
                    continue
                for (x0, x1), ch in zip(cells, val):
                    pool.setdefault(ch, []).append(g[:, x0:x1 + 1].copy())
                harvested.append((fdir, fn, si, val))
                kept += 1

    have = sorted(pool)
    miss = sorted(set("0123456789") - set(pool))
    print(f"\n采到 pool:有 {have}(各字样本数 { {c: len(pool[c]) for c in have} })  缺 {miss}")
    if miss:
        print(f"  ⚠️ 缺数字 {miss} → 这些数字读不出。补:在真值文件里加含这些数字的帧。")

    reader = digit_reader.DigitReader(exemplars=pool, ink_th=args.ink_th)
    reader.save(args.out)
    print(f"模板已存 {args.out}(score_th={reader.score_th}, ink_th={reader.ink_th})")

    # 自检:回读每个 harvest 帧的座,断言==真值(模板自洽)
    ok = bad = 0
    bads = []
    for fdir, fn, si, val in harvested:
        img = cv2.imread(str(fdir / fn))
        l, t, w, h = seat_rois[si]
        # live 读法是 classify 丢 '?'(位置无关),故自检用 allow_icon=icon_prefix 对齐 live
        got = reader.read(img[t:t + h, l:l + w], allow_icon=args.icon_prefix)
        if str(got) == val:
            ok += 1
        else:
            bad += 1
            bads.append((fn, si, val, got))
    print(f"\n自检(回读 harvest 帧):对 {ok} / 错 {bad}")
    for fn, si, val, got in bads[:20]:
        print(f"  ✗ {fn} s{si} 真值'{val}' → 读 {got}")
    if bad == 0:
        print("✅ 模板自洽(回读全对)。下一步:替 replay 跑 --digit-templates 看全下0能否读出。")

    # 留出验证:用建好的模板读【未参与采样】的录像,报真实泛化准度(95% 目标看这个,非自检)
    if args.validate_file and args.validate_session:
        vdir = Path(args.validate_session) / "frames"
        vblks = _parse_blocks(Path(args.validate_file).read_text(encoding="utf-8").splitlines())
        vok = vbad = 0
        vbads = []
        for fn, vals in vblks:
            for si, val in enumerate(vals):
                if not val or not val.isdigit() or si not in seat_rois:
                    continue
                img = cv2.imread(str(vdir / fn))
                if img is None:
                    continue
                l, t, w, h = seat_rois[si]
                got = reader.read(img[t:t + h, l:l + w], allow_icon=args.icon_prefix)
                if str(got) == val:
                    vok += 1
                else:
                    vbad += 1
                    vbads.append((fn, si, val, got))
        tot = vok + vbad
        pct = (100.0 * vok / tot) if tot else 0.0
        print(f"\n留出验证({Path(args.validate_file).name},未参与采样):"
              f"对 {vok} / 错 {vbad} = {pct:.1f}%  ← 这是真泛化准度(对标 95%)")
        for fn, si, val, got in vbads[:30]:
            print(f"  ✗ {fn} s{si} 真值'{val}' → 读 {got}")


if __name__ == "__main__":
    main()
