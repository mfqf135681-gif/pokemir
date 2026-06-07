"""tools/bench_recognize_wiring.py — #237 微基准:验证 recognize-only 的【接法 pattern】

背景:bench_ocr.py 的 ×3.1 是「一次 recognize() 传全部 ROI 框」测的;但 live 我接成了
「每座单独 recognize() 传 1 个框」。两种 pattern 的 per-call 开销摊销完全不同 → 必须单独测,
否则就是拿没验过的 pattern 上线(§2.0:性能数字必先 benchmark 真实 pattern)。

在【同一帧 + 同一批 action ROI】上隔离对比 4 种 pattern(剔除 live 牌桌活跃度混淆):
  A  baseline      : 逐座 read_text(crop, ensemble=True)        ← 现状 action 读
  B  my-wiring     : 逐座 recognize(整帧, [1 框])                ← 我 live 接的(可疑)
  C  batched-frame : 一次  recognize(整帧, [全部框])             ← bench 的 ×3.1 pattern
  D  per-crop      : 逐座 recognize(crop, [整 crop 框])          ← 不传整帧的逐座变体

读懂:若 B≈A(甚至更慢)而 C≪A → 坐实"逐座 1 框拿不到批量红利,要批才有肉"。
若 D≪A 而 B≈A → 坐实"传整帧是开销源,逐座也能快(但得传 crop)"。

⚠️ Win-only(需 GPU + easyocr)。Linux 仅验语法。
用法(Win,activated .venv):
  $env:POKEMIR_USE_GPU="1"
  python tools\\bench_recognize_wiring.py --frame "data\\recordings\\20260602_170343\\frames\\f_000100.png" --profile party_poker_8 --iters 30
"""

import argparse
import logging
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bench_recog_wiring")

ACTION_OCR_ALLOWLIST = "跟注让牌加下"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--iters", type=int, default=30)
    args = ap.parse_args()

    from capture.roi import ROIManager
    from recognition.ocr import OCREngine

    frame = cv2.imread(args.frame)
    if frame is None:
        log.error(f"读不到帧:{args.frame}"); sys.exit(2)
    H, W = frame.shape[:2]
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    log.info(f"帧 {W}x{H}")

    mgr = ROIManager.from_json(os.path.join("rois", f"{args.profile}.json"))
    # 收集 action_area 框(帧内坐标)+ 预切 crop —— 模拟 live action 读
    boxes, crops = [], []
    for sr in mgr.rois.seat_regions:
        a = getattr(sr, "action_area", None)
        if a is None or a.width <= 3 or a.height <= 3:
            continue
        x0, y0, x1, y1 = a.left, a.top, a.left + a.width, a.top + a.height
        if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
            continue
        boxes.append([x0, x1, y0, y1])
        crops.append(frame[y0:y1, x0:x1].copy())
    if not boxes:
        log.error("profile 里没提取到有效 action_area 框"); sys.exit(2)
    log.info(f"{len(boxes)} 个 action 框")

    ocr = OCREngine(gpu=True, name="bench")
    ocr.read_text(crops[0], allowlist=ACTION_OCR_ALLOWLIST)  # warm + _init
    grey_crops = [cv2.cvtColor(c, cv2.COLOR_BGR2GRAY) for c in crops]

    def bench(fn, n):
        fn()  # warm
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t0) / n * 1000.0  # ms/帧(全 action 框)

    # A baseline:逐座 read_text(crop, ensemble=True)
    msA = bench(lambda: [ocr.read_text(c, allowlist=ACTION_OCR_ALLOWLIST, ensemble=True) for c in crops], args.iters)
    # B my-wiring:逐座 recognize(整帧, [1 框])
    msB = bench(lambda: [ocr.recognize_boxes(grey, [b], allowlist=ACTION_OCR_ALLOWLIST) for b in boxes], args.iters)
    # C batched-frame:一次 recognize(整帧, [全部框])
    msC = bench(lambda: ocr.recognize_boxes(grey, boxes, allowlist=ACTION_OCR_ALLOWLIST), args.iters)
    # D per-crop:逐座 recognize(crop, [整 crop 框])
    def d_call():
        out = []
        for gc in grey_crops:
            ch, cw = gc.shape[:2]
            out.append(ocr.recognize_boxes(gc, [[0, cw, 0, ch]], allowlist=ACTION_OCR_ALLOWLIST))
        return out
    msD = bench(d_call, args.iters)

    n = len(boxes)
    print("\n========== recognize-only 接法 pattern 对比(全 action 框/帧)==========")
    print(f"action 框数: {n}   帧: {W}x{H}   iters: {args.iters}")
    print(f"A baseline   逐座 read_text(ensemble)  : {msA:8.1f} ms  ({msA/n:6.1f} ms/框)")
    print(f"B my-wiring  逐座 recognize(整帧,1框)   : {msB:8.1f} ms  ({msB/n:6.1f} ms/框)  ×{msA/msB:.2f} vs A")
    print(f"C batched    一次 recognize(整帧,全框)  : {msC:8.1f} ms  ({msC/n:6.1f} ms/框)  ×{msA/msC:.2f} vs A")
    print(f"D per-crop   逐座 recognize(crop,整框)  : {msD:8.1f} ms  ({msD/n:6.1f} ms/框)  ×{msA/msD:.2f} vs A")
    print("=" * 64)
    print("判读:B≈A(甚至更慢) ⟹ 我接的逐座1框拿不到红利,要批(C)才有肉。")
    print("      D≪A 而 B≈A     ⟹ 传整帧是开销源,逐座也能快但得传 crop。")
    print("      C≪A            ⟹ 批量 recognize 是真杠杆(印证 bench_ocr path C)。")

    # ── 准度对照:recognize-only 输出 vs read_text 输出(production 现用 read_text)──
    # 无 ground truth → 只报【一致性】(与现状读法一不一样),不报对错。
    base_txt = [ocr.read_text(c, allowlist=ACTION_OCR_ALLOWLIST, ensemble=True) for c in crops]
    bonly_txt = [ocr.recognize_boxes(grey, [b], allowlist=ACTION_OCR_ALLOWLIST)[0] for b in boxes]
    bat_txt = ocr.recognize_boxes(grey, boxes, allowlist=ACTION_OCR_ALLOWLIST)

    def norm(s):
        return (s or "").strip()
    nb = sum(1 for i in range(n) if norm(bonly_txt[i]) == norm(base_txt[i]))
    nc = sum(1 for i in range(n) if norm(bat_txt[i]) == norm(base_txt[i]))
    print("\n========== 准度一致性(recognize-only vs read_text 现状读法)==========")
    print(f"{'框':>3} | {'A read_text':>14} | {'B recog(整帧1框)':>16} | {'C recog(批)':>14} | B=A C=A")
    for i in range(n):
        a, b, c = norm(base_txt[i]), norm(bonly_txt[i]), norm(bat_txt[i])
        print(f"{i:>3} | {a[:14]:>14} | {b[:16]:>16} | {c[:14]:>14} |  {'✓' if b==a else '✗'}   {'✓' if c==a else '✗'}")
    print("-" * 64)
    print(f"B(逐座)与现状一致: {nb}/{n}   C(批量)与现状一致: {nc}/{n}")
    print("判读:一致率高 ⟹ recognize-only 不改读法,可拨默认;低 ⟹ 跳 otsu 伤准,需加回预处理。")
    print("注:本帧多为静态快照(idle 座显 ID / 部分空),空框两边都空也算一致;关注【非空框是否读得一样】。")


if __name__ == "__main__":
    main()
