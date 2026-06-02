"""tools/bench_ocr.py — OCR 吞吐 benchmark(T125 命门核心)

用一张真实录制帧 + ROI profile,对比三种 OCR 路径的每帧耗时 → 投影可达 Hz:
  A. 逐 ROI readtext(现状,每 ROI 一次 detect+recognize)
  B. readtext_batched(批处理,现已默认开)
  C. recognize-only 整帧一次(免检测 —— §7.1 命门假设:跳过检测能多快)

回答 T125 ②③:recognize-only 提速多少 + 全量读能冲几 Hz。

⚠️ Win-only(需 GPU + easyocr)。Linux 仅验语法。
用法(Win,先 $env:POKEMIR_USE_GPU="1"):
  .\\.venv\\Scripts\\python.exe tools\\bench_ocr.py --frame data\\recordings\\<ts>\\frames\\f_000100.png --profile party_poker_8 --iters 20
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# 让 `python tools/bench_ocr.py` 能 import 项目根的 recognition/config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bench_ocr")


# 牌/按钮区走 CNN/模板,不走 OCR → 测 OCR 吞吐时跳过
SKIP_KEYS = {"cards", "community_cards", "hero_card_1", "hero_card_2", "button_indicator"}


def collect_rois(obj, out):
    """递归找所有 [l,t,w,h] 4 元数组矩形;跳过 SKIP_KEYS(牌/按钮=非 OCR)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in SKIP_KEYS:
                continue
            collect_rois(v, out)
    elif isinstance(obj, list):
        if len(obj) == 4 and all(isinstance(x, (int, float)) for x in obj):
            out.append(tuple(int(x) for x in obj))
        else:
            for v in obj:
                collect_rois(v, out)


def main():
    ap = argparse.ArgumentParser(description="OCR 吞吐 benchmark(T125)")
    ap.add_argument("--frame", required=True, help="一张真实录制帧 PNG")
    ap.add_argument("--profile", default="party_poker_8", help="ROI profile 名(rois/<name>.json)")
    ap.add_argument("--iters", type=int, default=20, help="每模式迭代次数(取均值)")
    ap.add_argument("--allowlist", default="0123456789.弃跟加让下注牌kK万%", help="测速用合并 allowlist")
    args = ap.parse_args()

    try:
        import cv2
        import numpy as np
        from recognition.ocr import OCREngine
    except ImportError as e:
        log.error(f"缺依赖/路径 ({e})。在项目根目录、venv 里跑。"); sys.exit(2)

    frame = cv2.imread(args.frame)
    if frame is None:
        log.error(f"读不出帧: {args.frame}"); sys.exit(2)
    H, W = frame.shape[:2]
    log.info(f"帧 {W}x{H}")

    prof_path = Path("rois") / f"{args.profile}.json"
    if not prof_path.is_file():
        log.error(f"profile 不存在: {prof_path}"); sys.exit(2)
    prof = json.loads(prof_path.read_text(encoding="utf-8"))
    res = prof.get("resolution")
    if res and (W, H) != (int(res[0]), int(res[1])):
        log.warning(f"⚠️ 帧 {W}x{H} ≠ profile 分辨率 {res[0]}x{res[1]} → ROI 坐标会错位。"
                    f"测速仍代表(尺寸/数量对),但内容不对。**建议用 --window-title mss 录的窗口帧(={res[0]}x{res[1]})**。")
    rois = []
    collect_rois(prof, rois)
    # 只保留落在帧内、尺寸合理的 ROI
    rois = [(l, t, w, h) for (l, t, w, h) in rois if w > 3 and h > 3 and l >= 0 and t >= 0 and l + w <= W and t + h <= H]
    if not rois:
        log.error("profile 里没提取到有效 ROI(坐标可能超出此帧;确认帧与 profile 同分辨率/窗口)。"); sys.exit(2)
    crops = [frame[t:t + h, l:l + w].copy() for (l, t, w, h) in rois]
    log.info(f"{len(rois)} 个 ROI 从 profile 提取")

    ocr = OCREngine(gpu=True, name="bench")
    ocr.read_text(crops[0], allowlist=args.allowlist)  # 触发 _init + warm up
    reader = ocr._reader
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # recognize-only 的 horizontal_list:[x_min, x_max, y_min, y_max]
    hlist = [[l, l + w, t, t + h] for (l, t, w, h) in rois]

    def bench(fn, n):
        fn()  # warm
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t0) / n * 1000.0  # ms/iter

    log.info(f"benchmark {args.iters} 迭代/模式…(首次慢,取均值)")
    # A 逐 ROI readtext(现状)
    ms_per_roi = bench(lambda: [ocr.read_text(c, allowlist=args.allowlist) for c in crops], args.iters)
    # B 批处理
    ms_batch = bench(lambda: ocr.read_text_batch(crops, allowlist=args.allowlist), args.iters)
    # C recognize-only 整帧一次(免检测)
    ms_recog = bench(lambda: reader.recognize(grey, horizontal_list=hlist, free_list=[],
                                              detail=0, allowlist=args.allowlist), args.iters)

    def hz(ms):
        return 1000.0 / ms if ms > 0 else 0
    print("\n========== OCR 吞吐(每帧全 ROI)==========")
    print(f"ROI 数: {len(rois)}   帧: {W}x{H}")
    print(f"A 逐ROI readtext(现状)  : {ms_per_roi:8.1f} ms/帧 → {hz(ms_per_roi):5.2f} Hz")
    print(f"B readtext_batched       : {ms_batch:8.1f} ms/帧 → {hz(ms_batch):5.2f} Hz  (×{ms_per_roi/ms_batch:.1f} vs A)")
    print(f"C recognize-only 整帧一次: {ms_recog:8.1f} ms/帧 → {hz(ms_recog):5.2f} Hz  (×{ms_per_roi/ms_recog:.1f} vs A)")
    print("==========================================")
    print("注:C 用单一合并 allowlist 一次读完所有 ROI = 速度上界;真用需按 allowlist 分 2-3 组")
    print("    (仍远少于 A 的逐 ROI),实际介于 B/C 之间。目标看能否冲 4-10Hz。")


if __name__ == "__main__":
    main()
