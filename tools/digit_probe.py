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


def _match_char(glyph, templates, score_th):
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
    ap.add_argument("--label", required=True,
                    help='"帧文件:已知值, …"(该座该 ROI 真实数值,覆盖 0-9)')
    ap.add_argument("--decimate", type=int, default=10)
    ap.add_argument("--ink-th", type=int, default=150)
    ap.add_argument("--gap-th", type=int, default=0)
    ap.add_argument("--score-th", type=float, default=0.6)
    ap.add_argument("--dump-proj", action="store_true")
    args = ap.parse_args()

    import cv2

    prof = json.loads((Path(_ROOT) / "rois" / f"{args.profile}.json").read_text(encoding="utf-8"))
    seat = next(s for s in prof["seats"] if s["seat_index"] == args.seat)
    l, t, w, h = seat[args.field]
    fdir = Path(args.session) / "frames"

    def gray_roi(fn):
        img = cv2.imread(str(fdir / fn))
        if img is None:
            return None
        crop = img[t:t + h, l:l + w]
        return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop

    labels = []
    for pair in args.label.split(","):
        pair = pair.strip()
        if not pair:
            continue
        fn, val = pair.split(":")
        labels.append((fn.strip(), val.strip()))

    # ── ① 采模板(按真值打标签)──
    templates = {}
    for fn, val in labels:
        g = gray_roi(fn)
        if g is None:
            print(f"  ⚠️ 读不到 {fn}"); continue
        cells = digit_ocr.segment_cells(_col_ink(g, args.ink_th), args.gap_th)
        if args.dump_proj:
            print(f"  [proj] {fn} 真值'{val}' 切{len(cells)}格 {cells}\n         投影={_col_ink(g, args.ink_th)}")
        if len(cells) != len(val):
            print(f"  ⚠️ {fn}: 切{len(cells)}格 ≠ 真值{len(val)}位 → 跳过(切割没对齐,调 --ink-th/--gap-th)")
            continue
        for (x0, x1), ch in zip(cells, val):
            templates.setdefault(ch, g[:, x0:x1 + 1].copy())
    print(f"\n采到模板: {sorted(templates)}  缺 {sorted(set('0123456789') - set(templates))}")
    if not templates:
        print("没采到任何模板 —— 先用 --dump-proj 把切割调对(格数==位数)。"); return

    def tmpl_read(g):
        cells = digit_ocr.segment_cells(_col_ink(g, args.ink_th), args.gap_th)
        digits, _ = digit_ocr.parse_number(
            cells, lambda c: _match_char(g[:, c[0]:c[1] + 1], templates, args.score_th))
        return digits

    # ── ② 自检:label 帧回读 == 真值 ──
    print("\n=== ② 自检(label 帧模板回读 vs 真值)===")
    for fn, val in labels:
        g = gray_roi(fn)
        if g is None:
            continue
        r = tmpl_read(g)
        print(f"  {fn}: 模板读'{r}' vs 真值'{val}'  {'✅' if r == val else '❌'}")

    # ── ③ 泛化抽查:非 label 帧 dump ──
    manifest = (Path(args.session) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    frames = [json.loads(x) for x in manifest[1:] if x.strip()]
    label_files = {fn for fn, _ in labels}
    print("\n=== ③ 泛化抽查(非 label 帧,模板读,眼裁)===")
    shown = 0
    for k, d in enumerate(frames):
        if k % args.decimate or d["file"] in label_files:
            continue
        g = gray_roi(d["file"])
        if g is None:
            continue
        r = tmpl_read(g)
        if r:
            print(f"  t{d.get('t_mono',0):.1f} {d['file']}: '{r}'")
            shown += 1
        if shown >= 25:
            break
    print("\n判读:② 全 ✅ = 模板自洽;③ 开对应帧图眼裁读数对不对(尤其小额)。准 → 模板替 EasyOCR 成立。")


if __name__ == "__main__":
    main()
