"""tools/bench_cnn.py — 小 CNN 吞吐 benchmark(T125 命门第二块)

§12 证伪了 EasyOCR(~1Hz,recognize-only 不提速)。本 bench 测**现成识牌 CardCNN**
batched 跑 N 个 crop 多快 = §7.6「轻量模型单前向」速度假设的真检验。
够快(几十 ms)→ CNN 路能上高帧;不够 → 高帧路要重估。

两个数:
  - forward-only:batch 预建,纯 GPU 前向(吞吐上界)
  - full/帧:每帧 crop+transform+前向(现实成本,含预处理)
含 torch.cuda.synchronize()(GPU 异步,不同步会假快)。

⚠️ Win-only(GPU+torch+models/card_cnn.pth)。Linux 仅验语法。
用法(Win,$env:POKEMIR_USE_GPU="1"):
  .\\.venv\\Scripts\\python.exe tools\\bench_cnn.py --frame data\\recordings\\<ts>\\frames\\f_000003.png --profile party_poker_8 --iters 50
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bench_ocr import collect_rois  # noqa: E402  复用 ROI 提取

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bench_cnn")


def main():
    ap = argparse.ArgumentParser(description="小 CNN 吞吐 benchmark(T125)")
    ap.add_argument("--frame", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--iters", type=int, default=50)
    args = ap.parse_args()

    try:
        import cv2
        import numpy as np
        import torch
        from PIL import Image as PILImage
        from recognition.cnn_classifier import CnnClassifier
    except ImportError as e:
        log.error(f"缺依赖/路径 ({e})。项目根 + venv 里跑。"); sys.exit(2)

    frame = cv2.imread(args.frame)
    if frame is None:
        log.error(f"读不出帧 {args.frame}"); sys.exit(2)
    H, W = frame.shape[:2]
    prof = json.loads((Path("rois") / f"{args.profile}.json").read_text(encoding="utf-8"))
    rois = []
    collect_rois(prof, rois)
    rois = [(l, t, w, h) for (l, t, w, h) in rois if w > 3 and h > 3 and l >= 0 and t >= 0 and l + w <= W and t + h <= H]
    if not rois:
        log.error("无有效 ROI(帧与 profile 分辨率不符?用 --window-title mss 窗口帧)。"); sys.exit(2)
    log.info(f"帧 {W}x{H},{len(rois)} 个 ROI")

    clf = CnnClassifier()
    if not clf.available:
        log.error("CardCNN 不可用(models/card_cnn.pth 缺?torch?)。"); sys.exit(3)
    model, device, tf = clf._model, clf._device, clf._transform
    log.info(f"CardCNN on {device},输入 {clf._input_w}x{clf._input_h}")

    def crops_now():
        return [frame[t:t + h, l:l + w] for (l, t, w, h) in rois]

    def to_batch(crops):
        ts = []
        for c in crops:
            img = c[..., :3] if c.shape[2] == 4 else c
            rgb = np.ascontiguousarray(img[..., ::-1])  # BGR→RGB
            ts.append(tf(PILImage.fromarray(rgb).convert("RGB")))
        return torch.stack(ts).to(device)

    def sync():
        if device.type == "cuda":
            torch.cuda.synchronize()

    # 预建 batch(forward-only 用)
    batch = to_batch(crops_now())

    def fwd_only():
        with torch.no_grad():
            model(batch)
        sync()

    def full_frame():
        b = to_batch(crops_now())   # 每帧 crop+transform
        with torch.no_grad():
            model(b)
        sync()

    def bench(fn, n):
        fn(); sync()
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t0) / n * 1000.0

    log.info(f"benchmark {args.iters} 迭代/模式…")
    ms_fwd = bench(fwd_only, args.iters)
    ms_full = bench(full_frame, args.iters)

    def hz(ms):
        return 1000.0 / ms if ms > 0 else 0
    print("\n========== CNN 吞吐(每帧全 ROI batched)==========")
    print(f"ROI 数: {len(rois)}   帧: {W}x{H}   CNN 输入: {clf._input_w}x{clf._input_h}")
    print(f"forward-only(GPU 上界): {ms_fwd:7.1f} ms/帧 → {hz(ms_fwd):6.1f} Hz")
    print(f"full/帧(crop+transform+前向): {ms_full:7.1f} ms/帧 → {hz(ms_full):6.1f} Hz")
    print(f"对比 EasyOCR A(§12): 980 ms/帧 → 1.0 Hz   ⇒ CNN ×{980/ms_full:.0f} 提速(full)")
    print("===================================================")
    print("注:CardCNN 是为牌训的,这里只测吞吐(forward 速度),不看准度。")
    print("    若 full/帧 远超 4-10Hz → CNN 路可上高帧(§7.6 速度假设成立),剩下是为文字/数字训对应小 CNN。")
    print("    若 full 慢在 transform(PIL×N)→ 可换 cv2/torch 批量预处理优化。")


if __name__ == "__main__":
    main()
