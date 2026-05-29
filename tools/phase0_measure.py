"""Phase 0 — T51/T52 Pipeline 优化前的真实测量(Win 端跑).

目的:验证 T51(batch OCR)/ T52(pixel diff)的真实可行性 + 加速幅度,
**避免按假设盲做导致屎山**(V2 §15 教训应用).

测什么:
  1. EasyOCR `readtext_batched` API 是否真支持 + per-image allowlist 是否能用
  2. 单次 OCR vs 批量 OCR(8/16/32/50 个 ROI)的延迟对比
  3. cv2.absdiff(pixel diff)在 typical ROI 大小的延迟
  4. 模拟 pipeline 1 tick 全扫(8 seats × 5 ROI)的总延迟
  5. 模拟 pipeline 1 tick 用 diff 触发(只 OCR 变化 ROI)的总延迟

用法(Win):
  cd D:\\project\\pokemir
  .venv\\Scripts\\activate
  python tools\\phase0_measure.py --profile party_poker_8

输出:
  - 控制台 stdout(人类可读 + JSON 块)
  - tools/output/phase0_measure_<ts>.json(回传 VPS 用)

把 JSON 块复制贴给我,我做下一步 plan.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _percentile(values, p):
    """计算百分位(避免依赖 numpy 那一坨)."""
    if not values:
        return None
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * p)
    return sorted_v[min(idx, len(sorted_v) - 1)]


def _bench(label: str, fn, n_runs: int, warmup: int = 2) -> dict:
    """通用 benchmark 函数,跑 n_runs 次,统计 min/median/p95/avg."""
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)  # ms
    return {
        "label": label,
        "n_runs": n_runs,
        "min_ms": round(min(times), 2),
        "median_ms": round(_percentile(times, 0.5), 2),
        "p95_ms": round(_percentile(times, 0.95), 2),
        "max_ms": round(max(times), 2),
        "avg_ms": round(sum(times) / len(times), 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Phase 0 Pipeline 优化测量")
    parser.add_argument("--profile", default="party_poker_8")
    parser.add_argument("--runs", type=int, default=20, help="每个 benchmark 跑多少次")
    args = parser.parse_args()

    output_dir = PROJECT_ROOT / "tools" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"phase0_measure_{ts}.json"

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "profile": args.profile,
        "runs": args.runs,
        "tests": {},
    }

    print("=" * 70)
    print("Phase 0 — Pipeline 优化测量")
    print("=" * 70)

    # ────────────────────────────────────────
    # Test 1: EasyOCR 初始化 + API 探测
    # ────────────────────────────────────────
    print("\n[Test 1] EasyOCR 初始化 + API 探测")
    print("-" * 70)
    t0 = time.perf_counter()
    import easyocr
    reader = easyocr.Reader(["ch_sim", "en"], gpu=True)
    init_ms = (time.perf_counter() - t0) * 1000
    print(f"  Init time: {init_ms:.0f} ms")

    has_batched = hasattr(reader, "readtext_batched")
    has_recognize = hasattr(reader, "recognize")
    print(f"  reader.readtext_batched 存在: {has_batched}")
    print(f"  reader.recognize 存在: {has_recognize}")

    import inspect
    batched_sig = None
    if has_batched:
        try:
            batched_sig = str(inspect.signature(reader.readtext_batched))
            print(f"  readtext_batched signature: {batched_sig}")
        except (TypeError, ValueError):
            batched_sig = "introspection failed"

    report["tests"]["api_probe"] = {
        "init_ms": round(init_ms, 0),
        "has_readtext_batched": has_batched,
        "has_recognize": has_recognize,
        "batched_signature": batched_sig,
    }

    # ────────────────────────────────────────
    # Test 2: 加载现实 ROI 截图,构造测试图集
    # ────────────────────────────────────────
    print("\n[Test 2] 准备测试图集")
    print("-" * 70)
    # 用 capturer 抓当前屏一张 → 切若干 ROI
    from capture.screen import ScreenCapturer
    from capture.roi import _tuple_to_roi  # type: ignore

    roi_path = PROJECT_ROOT / "rois" / f"{args.profile}.json"
    if not roi_path.exists():
        print(f"  ERROR: profile not found {roi_path}")
        return 1
    with open(roi_path, encoding="utf-8") as f:
        rois_data = json.load(f)

    capturer = ScreenCapturer()
    window_title = rois_data.get("window_title", "")
    if window_title and not capturer.find_window_by_title(window_title):
        print(f"  ⚠️ 找不到窗口 {window_title!r},改 monitor 1")
        capturer.select_monitor(1)
    elif not window_title:
        capturer.select_monitor(1)

    full_img = capturer.capture()
    full_bgr = cv2.cvtColor(full_img, cv2.COLOR_BGRA2BGR)
    print(f"  Captured: {full_bgr.shape}")

    # 提取所有 seat sub-ROIs:stack / fold_area / action / amount / id
    test_imgs = []  # 真实 ROI 大小不同
    roi_keys = ("stack", "fold_area", "action", "amount", "id")
    for seat in rois_data.get("seats", []):
        for k in roi_keys:
            r = seat.get(k)
            if isinstance(r, list) and len(r) == 4 and r[2] > 0 and r[3] > 0:
                x, y, w, h = r
                if y + h <= full_bgr.shape[0] and x + w <= full_bgr.shape[1]:
                    crop = full_bgr[y:y+h, x:x+w].copy()
                    test_imgs.append((f"seat_{seat['seat_index']}_{k}", crop))
    print(f"  抽取 {len(test_imgs)} 个真实 ROI(typical pipeline 8 座 × 5 元素 = 40)")

    # 还要测 batch 用的 "uniform size" 版本(80×40 = 跟 action 区差不多)
    uniform_imgs = [cv2.resize(img, (80, 40)) for _, img in test_imgs[:50]]
    print(f"  Uniform 80×40 版本: {len(uniform_imgs)} 张")

    report["tests"]["roi_count"] = {
        "real_rois": len(test_imgs),
        "uniform_rois": len(uniform_imgs),
        "full_capture_shape": list(full_bgr.shape),
    }

    # ────────────────────────────────────────
    # Test 3: 单 OCR 延迟(baseline)
    # ────────────────────────────────────────
    print("\n[Test 3] 单 OCR 延迟(baseline)")
    print("-" * 70)
    sample = test_imgs[0][1] if test_imgs else uniform_imgs[0]
    r3 = _bench(
        "single_ocr_no_allowlist",
        lambda: reader.readtext(sample, detail=0),
        n_runs=args.runs,
    )
    print(f"  无 allowlist:  median={r3['median_ms']}ms  p95={r3['p95_ms']}ms")

    r3b = _bench(
        "single_ocr_digit_allowlist",
        lambda: reader.readtext(sample, detail=0, allowlist="0123456789"),
        n_runs=args.runs,
    )
    print(f"  digit allowlist: median={r3b['median_ms']}ms  p95={r3b['p95_ms']}ms")
    report["tests"]["single_ocr"] = {"no_allowlist": r3, "digit_allowlist": r3b}

    # ────────────────────────────────────────
    # Test 4: batch OCR (if API exists)
    # ────────────────────────────────────────
    print("\n[Test 4] Batch OCR (readtext_batched)")
    print("-" * 70)
    if has_batched and len(uniform_imgs) >= 8:
        batch_results = {}
        for batch_n in [8, 16, 32, 50]:
            if batch_n > len(uniform_imgs):
                continue
            imgs_subset = uniform_imgs[:batch_n]
            try:
                r4 = _bench(
                    f"batch_{batch_n}",
                    lambda imgs=imgs_subset: reader.readtext_batched(
                        imgs, n_width=80, n_height=40, batch_size=batch_n, detail=0
                    ),
                    n_runs=max(5, args.runs // 4),  # batch 慢,跑少点
                )
                batch_results[f"n={batch_n}"] = r4
                per_img = r4["median_ms"] / batch_n
                print(f"  N={batch_n}: median={r4['median_ms']}ms,"
                      f" per-img={per_img:.1f}ms")
            except Exception as e:
                print(f"  N={batch_n}: FAILED — {type(e).__name__}: {e}")
                batch_results[f"n={batch_n}"] = {"error": str(e)}

        # 测 batch 是否支持 per-image allowlist(关键)
        print("\n  → 测 batch + allowlist 兼容性:")
        try:
            test_imgs_8 = uniform_imgs[:8]
            reader.readtext_batched(
                test_imgs_8, n_width=80, n_height=40, batch_size=8,
                detail=0, allowlist="0123456789",
            )
            print("  ✅ batch 支持 allowlist 参数(全统一)")
            batch_results["allowlist_support"] = "uniform_only"
        except Exception as e:
            print(f"  ❌ batch + allowlist FAILED: {type(e).__name__}: {e}")
            batch_results["allowlist_support"] = f"failed: {e}"

        report["tests"]["batch_ocr"] = batch_results
    else:
        print("  跳过(API 不存在或图像不够)")
        report["tests"]["batch_ocr"] = {"skipped": "api_missing_or_insufficient_imgs"}

    # ────────────────────────────────────────
    # Test 5: cv2 pixel diff 延迟
    # ────────────────────────────────────────
    print("\n[Test 5] cv2 pixel diff 延迟")
    print("-" * 70)
    if len(test_imgs) >= 2:
        img_a = test_imgs[0][1]
        # 制造一个"几乎一样"的 img_b(差几像素)
        img_b = img_a.copy()
        img_b[0:5, 0:5] = 0
        r5 = _bench(
            "cv2_absdiff_one_roi",
            lambda: cv2.absdiff(img_a, img_b).sum(),
            n_runs=args.runs * 5,  # 这个超快,多跑
        )
        print(f"  cv2.absdiff + sum: median={r5['median_ms']}ms")

        r5b = _bench(
            "cv2_absdiff_40_rois",
            lambda: [cv2.absdiff(im[1], im[1]).sum() for im in test_imgs[:40]],
            n_runs=args.runs,
        )
        print(f"  40 ROI diff(sequential): median={r5b['median_ms']}ms")
        report["tests"]["pixel_diff"] = {"single_roi": r5, "forty_rois": r5b}

    # ────────────────────────────────────────
    # Test 6: 模拟 pipeline 全扫 vs diff-trigger
    # ────────────────────────────────────────
    print("\n[Test 6] 模拟 pipeline tick(40 ROI 全扫 vs diff-trigger)")
    print("-" * 70)
    if test_imgs:
        sample_imgs = [im[1] for im in test_imgs[:40]]

        # 全扫:40 个 OCR sequential
        def full_scan():
            for img in sample_imgs:
                reader.readtext(img, detail=0)
        r6 = _bench("full_scan_40_seq", full_scan, n_runs=max(3, args.runs // 5))
        print(f"  全扫 40 ROI 串行:median={r6['median_ms']}ms"
              f" ≈ {r6['median_ms']/1000:.2f}s")

        # diff-trigger 模拟:假设 90% ROI 不变,10% 变化才 OCR
        def diff_trigger():
            for i, img in enumerate(sample_imgs):
                # 模拟 diff 判断
                cv2.absdiff(img, img).sum()
                # 10% 概率"有变化"才 OCR
                if i % 10 == 0:
                    reader.readtext(img, detail=0)
        r6b = _bench("diff_trigger_4_of_40", diff_trigger, n_runs=max(3, args.runs // 5))
        print(f"  diff-trigger(只 4/40 OCR):median={r6b['median_ms']}ms"
              f" ≈ {r6b['median_ms']/1000:.2f}s")

        speedup = r6["median_ms"] / r6b["median_ms"] if r6b["median_ms"] > 0 else None
        print(f"  Speedup(diff vs 全扫): {speedup:.1f}x" if speedup else "")
        report["tests"]["pipeline_simulation"] = {
            "full_scan_40_seq": r6,
            "diff_trigger_4_of_40": r6b,
            "speedup_ratio": round(speedup, 2) if speedup else None,
        }

    # ────────────────────────────────────────
    # 写报告
    # ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Summary JSON(复制下方块给 Claude):")
    print("=" * 70)
    summary = {
        "api_has_readtext_batched": report["tests"]["api_probe"]["has_readtext_batched"],
        "single_ocr_median_ms": report["tests"]["single_ocr"]["no_allowlist"]["median_ms"],
        "single_ocr_p95_ms": report["tests"]["single_ocr"]["no_allowlist"]["p95_ms"],
        "batch_50_median_ms": (
            report["tests"]["batch_ocr"].get("n=50", {}).get("median_ms")
            if "n=50" in report["tests"].get("batch_ocr", {})
            else None
        ),
        "batch_allowlist_support": (
            report["tests"]["batch_ocr"].get("allowlist_support")
            if "batch_ocr" in report["tests"]
            else None
        ),
        "pixel_diff_one_roi_ms": (
            report["tests"]["pixel_diff"]["single_roi"]["median_ms"]
            if "pixel_diff" in report["tests"]
            else None
        ),
        "full_scan_40_seq_ms": (
            report["tests"]["pipeline_simulation"]["full_scan_40_seq"]["median_ms"]
            if "pipeline_simulation" in report["tests"]
            else None
        ),
        "diff_trigger_4_of_40_ms": (
            report["tests"]["pipeline_simulation"]["diff_trigger_4_of_40"]["median_ms"]
            if "pipeline_simulation" in report["tests"]
            else None
        ),
        "speedup_ratio": (
            report["tests"]["pipeline_simulation"].get("speedup_ratio")
            if "pipeline_simulation" in report["tests"]
            else None
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  完整报告:{output_path}")

    print("\n下一步:把上方 Summary JSON 块复制贴给 Claude,基于真数据制定 T51/T52 实施 plan")


if __name__ == "__main__":
    main()
