r"""tools/probe_zero.py — 全下"0"读 None 病因 A/B 探针(Win-only,cv2)。

回答一个具体问题:全下玩家筹码区显示一个【普通的"0"】,为什么回放的
EasyOCR(read_stack)读成 None / 空?数字模板配方(digit_ocr 的列墨切格)
能不能在 EasyOCR 检测失败的地方切出这个"0"格?

对【同一帧、同一座、同一 ROI】并排跑:
  A. EasyOCR(回放现用法,OTSU 预处理 + readtext)→ 打印 repr;
  B. 配方第一步:亮度归一 + 列墨投影 + segment_cells → 打印切出几格、各格宽;
     —— 这一步【不需要预采模板】,只验"检测/切割"能否在孤立 0 上找到字形区。
  并把 crop 放大存 PNG 供你眼裁。

⚠️ 全是短参数、值里无空格 → 复制到 PowerShell 不会被塞空格坑。
⚠️ cv2 + frames 都在 Win;Linux 无法验证(盲点),纯逻辑 segment_cells 已单测。

用法(Win,先 git pull):
  # 单帧 A/B(你已知某帧 seat2 显示 0):
  .\.venv\Scripts\python.exe tools\probe_zero.py --session data\recordings\20260602_170343 ^
      --frame f_000400.png --seat 2

  # 不知道哪帧?先扫:报该座 EasyOCR 读空的帧名(供上面 --frame 用):
  .\.venv\Scripts\python.exe tools\probe_zero.py --session data\recordings\20260602_170343 ^
      --seat 2 --find --gpu
"""
import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "pipeline"))
import digit_ocr  # noqa: E402  纯切割核(已单测)


def _seat_roi(prof, seat, field):
    for s in prof["seats"]:
        if s["seat_index"] == seat and s.get(field):
            return s[field]  # [l, t, w, h]
    raise SystemExit(f"profile 里 seat{seat} 没有 {field} ROI")


def _crop(img, roi):
    l, t, w, h = roi
    return img[t:t + h, l:l + w]


def _normalize(g):
    """配方默认的亮度归一(p2–p98 拉满量程)。"""
    import numpy as np
    lo, hi = float(np.percentile(g, 2)), float(np.percentile(g, 98))
    if hi - lo >= 30:
        g = ((g.astype(np.float32) - lo) / (hi - lo) * 255).clip(0, 255).astype(np.uint8)
    return g


def _col_ink(gray, ink_th=150):
    import numpy as np
    return (gray > ink_th).astype(np.uint8).sum(axis=0).tolist()


def main():
    ap = argparse.ArgumentParser(description="全下0读None病因A/B探针(EasyOCR vs 配方切格)")
    ap.add_argument("--session", required=True, help=r"录像目录,如 data\recordings\<ts>")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--seat", type=int, required=True)
    ap.add_argument("--field", default="stack")
    ap.add_argument("--frame", default="", help="帧文件名,如 f_000400.png(--find 时可省)")
    ap.add_argument("--gpu", action="store_true", help="EasyOCR 走 GPU(与回放一致)")
    ap.add_argument("--find", action="store_true",
                    help="扫描该座、报 EasyOCR 读空的帧名(供 --frame 用)")
    ap.add_argument("--decimate", type=int, default=10, help="--find 每 N 帧扫一次")
    ap.add_argument("--limit", type=int, default=30, help="--find 最多报几帧")
    ap.add_argument("--ink-th", type=int, default=150)
    ap.add_argument("--templates", default="",
                    help="模板 JSON(build_digit_templates.py 产):用 DigitReader 实际读该0,验兜底能否补出")
    ap.add_argument("--out", default="probe_zero_crop.png", help="放大 crop 存盘路径")
    args = ap.parse_args()

    import cv2
    import numpy as np

    prof = json.loads((Path(_ROOT) / "rois" / f"{args.profile}.json").read_text(encoding="utf-8"))
    roi = _seat_roi(prof, args.seat, args.field)
    fdir = Path(args.session) / "frames"

    from recognition.ocr import OCREngine
    eng = OCREngine(gpu=args.gpu, default_allowlist="0123456789")

    def easyocr_read(crop):
        return eng.read_text(crop, allowlist="0123456789")

    # ── --find:扫整段,报该座 EasyOCR 读空(空串)的帧 ──
    if args.find:
        mpath = Path(args.session) / "manifest.jsonl"
        lines = mpath.read_text(encoding="utf-8").splitlines()
        frames = [json.loads(x) for x in lines[1:] if x.strip()]
        picks = frames[::max(1, args.decimate)]
        print(f"扫 seat{args.seat} {args.field}(共 {len(frames)} 帧,每 {args.decimate} 取一,EasyOCR gpu={args.gpu}):")
        empties, n = [], 0
        for d in picks:
            img = cv2.imread(str(fdir / d["file"]))
            if img is None:
                continue
            txt = easyocr_read(_crop(img, roi))
            n += 1
            if txt == "":
                empties.append((d["file"], d.get("t_mono", 0)))
                if len(empties) <= args.limit:
                    print(f"  空 ← {d['file']}  t={d.get('t_mono',0):.0f}s")
        print(f"\n读了 {n} 帧,空 {len(empties)} 帧。挑一个上面的帧名跑:")
        print(f"  ...probe_zero.py --session {args.session} --frame <帧> --seat {args.seat}")
        return

    if not args.frame:
        raise SystemExit("需要 --frame <帧文件名>(或先 --find 找读空帧)")

    img = cv2.imread(str(fdir / args.frame))
    if img is None:
        raise SystemExit(f"读不到帧 {fdir / args.frame}")
    crop = _crop(img, roi)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop

    print(f"=== seat{args.seat} {args.field} @ {args.frame}  ROI={roi} ===\n")

    # A. EasyOCR(回放现法)
    a = easyocr_read(crop)
    print(f"[A] EasyOCR(gpu={args.gpu}, allowlist=0-9) → {a!r}"
          + ("   ← 读空/None(复现病象)" if a == "" else ""))

    # B. 配方第一步:归一 + 列墨 + 切格(不需模板,只验检测/切割)
    gnorm = _normalize(gray.copy())
    ink = _col_ink(gnorm, args.ink_th)
    cells = digit_ocr.segment_cells(ink, gap_th=0, min_gap=2, min_cell_w=3, max_merge_w=14)
    print(f"[B] 配方 归一+列墨(ink_th={args.ink_th})+segment_cells → 切出 {len(cells)} 格"
          + (f",各格(起,止,宽): {[(a0, b0, b0 - a0) for a0, b0 in cells]}" if cells else ""))
    print(f"    列墨投影(每列墨像素数): {ink}")
    if cells:
        print("    → 配方在 EasyOCR 失败处【切出了字形区】,佐证根因=EasyOCR检测漏掉孤立0,"
              "配方可补(下一步:采 0 模板回读确认字符)。")
    else:
        print("    → 配方也没切出格:可能 ROI 真空 / 数字非白(调 --ink-th)/ 框没盖到 0。")

    # C. 带模板实读(--templates):验兜底 DigitReader 能否把这个 0 读出来
    if args.templates:
        sys.path.insert(0, os.path.join(_ROOT, "pipeline"))
        import digit_reader
        rdr = digit_reader.DigitReader.load(args.templates)
        v = rdr.read(crop)
        print(f"\n[C] DigitReader(模板 {args.templates}, 字符 {sorted(rdr.exemplars)}).read → {v!r}"
              + ("   ✅ 兜底读出!换配方读 stack 成立" if v is not None
                 else "   ✗ 仍 None → 跨录像/跨座模板没迁过来(需补该侧座的0模板)"))

    # 放大 crop 存盘(6x,画切格红线)供眼裁
    vis = cv2.resize(crop, (crop.shape[1] * 6, crop.shape[0] * 6), interpolation=cv2.INTER_NEAREST)
    for a0, b0 in cells:
        cv2.line(vis, (a0 * 6, 0), (a0 * 6, vis.shape[0]), (0, 0, 255), 1)
        cv2.line(vis, (b0 * 6, 0), (b0 * 6, vis.shape[0]), (0, 255, 0), 1)
    cv2.imwrite(args.out, vis)
    print(f"\n放大 crop(6x,红=格起/绿=格止)已存 {args.out} —— 打开核对是不是那个普通 0。")


if __name__ == "__main__":
    main()
