r"""tools/digit_probe.py — 数字模板 OCR 探针(验 Option2:模板匹配 vs EasyOCR,单 ROI)。

流程(单座单 ROI,默认 seat0.stack):
  ① 自举采模板:EasyOCR 读数字,投影切割,若【格数==位数】则按 OCR 标的数字存每个字形(首个干净实例)。
  ② 模板读:每帧 投影切割 → 逐格对模板归一化相关匹配 → 数字串。
  ③ 比对:模板读 vs EasyOCR 读 → 一致率 + 分歧清单(你眼裁定谁对);重点看小额(段2 2筹码)。
  --dump:打印前几帧的 列投影 + 切出的格,肉眼确认切割(有缝)对不对,再信准确率。

⚠️ cv2/EasyOCR,**未在 Linux 验证**(本机无 cv2)。纯切割/解析逻辑在 pipeline/digit_ocr.py(已单测)。
用法:
  .\.venv\Scripts\python.exe tools\digit_probe.py --session data\recordings\<ts> --seat 0 --field stack [--dump] [--decimate 5]
"""
import argparse
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "pipeline"))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import digit_ocr  # noqa: E402  纯核(已单测)


def _col_ink(gray, ink_th):
    """列墨色:每列【亮】像素数(WePoker 数字浅色压深底 → 亮=墨)。gray: 2D uint8。"""
    import numpy as np
    mask = (gray > ink_th).astype(np.uint8)
    return mask.sum(axis=0).tolist()


def _match_char(glyph, templates, score_th):
    """glyph(灰度 2D)对 templates{char: 灰度模板} 归一化相关 → 最佳字符或 '?'。"""
    import cv2
    import numpy as np
    best, best_s = "?", -1.0
    for ch, tmpl in templates.items():
        g = cv2.resize(glyph, (tmpl.shape[1], tmpl.shape[0]))
        gf, tf = g.astype(np.float32), tmpl.astype(np.float32)
        gf -= gf.mean(); tf -= tf.mean()
        denom = (np.linalg.norm(gf) * np.linalg.norm(tf)) or 1.0
        s = float((gf * tf).sum() / denom)        # 归一化互相关 [-1,1]
        if s > best_s:
            best, best_s = ch, s
    return best if best_s >= score_th else "?"


def main():
    ap = argparse.ArgumentParser(description="数字模板 OCR 探针(Option2 验证)")
    ap.add_argument("--session", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--seat", type=int, default=0)
    ap.add_argument("--field", default="stack", help="seat 子 ROI(stack/amount/...)")
    ap.add_argument("--decimate", type=int, default=5)
    ap.add_argument("--ink-th", type=int, default=150, help="亮度>此=墨(浅字深底)")
    ap.add_argument("--gap-th", type=int, default=0, help="列墨色<=此=缝")
    ap.add_argument("--score-th", type=float, default=0.6, help="相关<此判'?'")
    ap.add_argument("--dump", action="store_true", help="打印前几帧投影+切格,验切割")
    args = ap.parse_args()

    import cv2
    import json
    from recognition.ocr import OCREngine

    prof = json.loads((Path(_ROOT) / "rois" / f"{args.profile}.json").read_text(encoding="utf-8"))
    seat = next(s for s in prof["seats"] if s["seat_index"] == args.seat)
    roi = seat[args.field]
    l, t, w, h = roi
    manifest = (Path(args.session) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    frames = [json.loads(x) for x in manifest[1:] if x.strip()]
    fdir = Path(args.session) / "frames"
    ocr = OCREngine(gpu=True, name="digit_probe")

    def read_roi(img):
        crop = img[t:t + h, l:l + w]
        return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop

    def easy_digits(gray):
        txt = ocr.read_text(gray, allowlist="0123456789")
        return "".join(c for c in txt if c.isdigit())

    # ── ① 自举采模板 ──
    templates, dumped = {}, 0
    for k, d in enumerate(frames):
        if k % args.decimate:
            continue
        img = cv2.imread(str(fdir / d["file"]))
        if img is None:
            continue
        gray = read_roi(img)
        digits = easy_digits(gray)
        col = _col_ink(gray, args.ink_th)
        cells = digit_ocr.segment_cells(col, args.gap_th)
        if args.dump and dumped < 5:
            print(f"  [dump] {d['file']} OCR='{digits}' 切{len(cells)}格 {cells}  投影={col}")
            dumped += 1
        if digits and len(cells) == len(digits):
            for (x0, x1), ch in zip(cells, digits):
                if ch not in templates:
                    templates[ch] = gray[:, x0:x1 + 1].copy()
    print(f"\n采到模板字符: {sorted(templates)}  (缺 {sorted(set('0123456789') - set(templates))})")
    if len(templates) < 10:
        print("⚠️ 模板不全 → 该 ROI 数值多样性不够 / 切割与 OCR 对不齐;先看 --dump 切割对不对")

    # ── ②③ 模板读 vs EasyOCR + 比对 ──
    agree = total = 0
    disagree = []
    for k, d in enumerate(frames):
        if k % args.decimate:
            continue
        img = cv2.imread(str(fdir / d["file"]))
        if img is None:
            continue
        gray = read_roi(img)
        eo = easy_digits(gray)
        col = _col_ink(gray, args.ink_th)
        cells = digit_ocr.segment_cells(col, args.gap_th)
        tmpl_read, _ = digit_ocr.parse_number(
            cells, lambda c: _match_char(gray[:, c[0]:c[1] + 1], templates, args.score_th))
        if not eo and not tmpl_read:
            continue
        total += 1
        if tmpl_read == eo:
            agree += 1
        else:
            disagree.append((round(d.get("t_mono", 0), 1), tmpl_read, eo))
    rate = agree / total if total else float("nan")
    print(f"\n=== 模板 vs EasyOCR(seat{args.seat}.{args.field})===")
    print(f"  一致 {agree}/{total} = {rate*100:.0f}%")
    print(f"  分歧(t, 模板读, EasyOCR读)前 30:\n  {disagree[:30]}")
    print("\n判读:分歧处你眼裁定谁对(尤其小额/2筹码);一致率高+模板在小额上更准 → 模板可替 EasyOCR。")


if __name__ == "__main__":
    main()
