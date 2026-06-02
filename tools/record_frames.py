"""tools/record_frames.py — 原始帧录制(T126 测量地基,步骤 1)

目的:把 WePoker 牌桌窗口区域按目标帧率录成**无损帧序列 + manifest**,供下游:
  - A/B 新旧管线(同一批帧喂两边,公平对比)
  - ground-truth 人工标注(逐手看真值 → 算捕获率)
  - "永不渲染"地板测量(数有多少动作肉眼可见却没有可读帧)
  - 可复现测试(回放固定帧序列)

设计 doc: requirement-discussions/2026-06-01_95pct-constraint-solver-paradigm.md §8/§9
任务: T126

──────────────────────────────────────────────────────────────────────
⚠️ 未经执行验证(WINDOWS-ONLY) — 本文件在 Linux 写就,作者无法运行 DXcam。
   首次必须在 Win 端按文末 [WIN 验证 CHECKLIST] 逐条确认,尤其黑屏/区域/颜色序。
──────────────────────────────────────────────────────────────────────

红线合规:
  R-1 (image-only): DXcam = Desktop Duplication 截屏,**无内存读取 / 无 DOM / 无抓包**。✓
  R-3 (PII 最小化): ⚠️ 录**整窗** → 会把其他玩家昵称/聊天一并存盘(比 live pipeline 的瞬时处理更敏感)。
       缓解:① 用 --region 把聊天面板排除在外;② 录制仅本地自用、标注完即删(见 --note);
       ③ 这是你自己屏幕上自己牌局的画面。**是否录、录多久由你按治理框架定。**

依赖(Win venv):  pip install dxcam pygetwindow opencv-python numpy

用法(Win PowerShell):
  # 按窗口标题自动定位,10fps 录最多 5 分钟,存 PNG
  .\\.venv\\Scripts\\python.exe tools\\record_frames.py --window-title "WePoker" --fps 10 --duration 300
  # 显式屏幕绝对坐标(left top width height),排除右侧聊天
  .\\.venv\\Scripts\\python.exe tools\\record_frames.py --region 100 80 1280 720 --fps 8
  # 多显示器黑屏时,试不同 output-idx
  .\\.venv\\Scripts\\python.exe tools\\record_frames.py --window-title "WePoker" --output-idx 1
"""

import argparse
import json
import logging
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("record_frames")


def resolve_region(args):
    """→ (left, top, right, bottom) 屏幕绝对坐标;或 None 表示全屏。"""
    if args.region:
        l, t, w, h = args.region
        return (l, t, l + w, t + h)
    if args.window_title:
        try:
            import pygetwindow as gw
        except ImportError:
            log.error("缺 pygetwindow:pip install pygetwindow(或用 --region 显式给坐标)")
            sys.exit(2)
        wins = [w for w in gw.getWindowsWithTitle(args.window_title) if w.title.strip()]
        if not wins:
            log.error(f"找不到标题含 '{args.window_title}' 的窗口。开着 WePoker 吗?或用 --region。")
            sys.exit(2)
        win = wins[0]
        log.info(f"命中窗口: '{win.title}'  ({win.left},{win.top}) {win.width}x{win.height}")
        if win.width <= 0 or win.height <= 0 or win.left < -10000:
            log.error("窗口尺寸异常(最小化?)。先还原窗口,或用 --region。")
            sys.exit(2)
        return (win.left, win.top, win.left + win.width, win.top + win.height)
    return None  # 全屏(不推荐:更易碰 R-3 + 体积大)


def main():
    ap = argparse.ArgumentParser(description="录 WePoker 窗口为无损帧序列 + manifest(T126)")
    src = ap.add_argument_group("捕获区域(二选一,都不给=全屏)")
    src.add_argument("--window-title", type=str, help="按标题自动定位窗口(pygetwindow)")
    src.add_argument("--region", type=int, nargs=4, metavar=("L", "T", "W", "H"),
                     help="屏幕绝对坐标 left top width height")
    ap.add_argument("--fps", type=float, default=10.0, help="目标帧率(默认 10;DXcam 上限 ~120)")
    ap.add_argument("--duration", type=float, default=300.0, help="最长录制秒数(默认 300)")
    ap.add_argument("--max-frames", type=int, default=12000, help="帧数硬上限(防写满盘,默认 12000)")
    ap.add_argument("--out-dir", type=str, default="data/recordings", help="输出根目录")
    ap.add_argument("--format", choices=["png", "jpg"], default="png",
                    help="png=无损(replay 用,推荐) / jpg=小但有损(OCR 慎用)")
    ap.add_argument("--output-idx", type=int, default=None, help="DXcam 显示器输出号(多屏黑屏时试 0/1)")
    ap.add_argument("--device-idx", type=int, default=None, help="DXcam GPU 设备号(多卡时)")
    ap.add_argument("--min-free-gb", type=float, default=5.0, help="剩余磁盘低于此 GB 即停")
    ap.add_argument("--note", type=str, default="", help="录入 manifest 的备注(如牌局/桌型)")
    args = ap.parse_args()

    try:
        import cv2
        import numpy as np
        import dxcam
    except ImportError as e:
        log.error(f"缺依赖 ({e}). Win venv: pip install dxcam opencv-python numpy")
        sys.exit(2)

    region = resolve_region(args)

    # session 目录:data/recordings/YYYYmmdd_HHMMSS/
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out_dir) / stamp
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"输出: {out.resolve()}")

    # DXcam 相机(BGR 顺序 → 直接喂 cv2.imwrite 颜色正确)
    cam_kwargs = {"output_color": "BGR"}
    if args.output_idx is not None:
        cam_kwargs["output_idx"] = args.output_idx
    if args.device_idx is not None:
        cam_kwargs["device_idx"] = args.device_idx
    camera = dxcam.create(**cam_kwargs)
    if camera is None:
        log.error("dxcam.create 返回 None — 检查 output-idx/device-idx(多屏多卡)。")
        sys.exit(3)

    # 黑屏自检(§7.3 已知坑):抓一帧,全黑/读不到 → 大声失败,别录一堆废帧
    probe = camera.grab(region=region) if region else camera.grab()
    if probe is None:
        log.error("首帧 None。常见:受保护/硬件加速窗口、output-idx 错。换 --output-idx 再试。")
        sys.exit(3)
    if float(probe.mean()) < 2.0:
        log.error(f"首帧近乎全黑(mean={probe.mean():.2f})。多屏/输出号不对的典型症状。"
                  f"换 --output-idx(0/1/...)再试。")
        sys.exit(3)
    log.info(f"黑屏自检通过 (帧 {probe.shape}, mean={probe.mean():.1f}). 开录…  Ctrl-C 停止。")

    # 估算体积 + 磁盘守卫
    free_gb = shutil.disk_usage(out).free / 1e9
    log.info(f"剩余磁盘 {free_gb:.1f} GB;{args.fps:.0f}fps × {args.duration:.0f}s ≈ "
             f"{int(args.fps * args.duration)} 帧(png 约 1-2MB/帧)。低于 {args.min_free_gb}GB 自动停。")

    manifest_path = out / "manifest.jsonl"
    meta = {
        "_meta": True, "stamp": stamp, "region": region, "fps": args.fps,
        "format": args.format, "note": args.note,
        "started_wall": datetime.now(timezone.utc).isoformat(),
        "probe_shape": list(probe.shape), "output_idx": args.output_idx,
        "tool": "record_frames.py", "task": "T126",
    }

    camera.start(region=region, target_fps=int(round(args.fps)), video_mode=True)
    n = 0
    t0 = time.perf_counter()
    last_disk_check = t0
    try:
        with manifest_path.open("w", encoding="utf-8") as mf:
            mf.write(json.dumps(meta, ensure_ascii=False) + "\n")
            while True:
                frame = camera.get_latest_frame()  # 阻塞到下一新帧,按 target_fps 节拍
                t = time.perf_counter()
                if frame is None:
                    continue
                fname = f"f_{n:06d}.{args.format}"
                ok = cv2.imwrite(str(frames_dir / fname), frame)
                if not ok:
                    log.error(f"写帧失败 {fname}(磁盘满?路径?). 停。")
                    break
                mf.write(json.dumps({
                    "i": n, "file": fname,
                    "t_mono": round(t - t0, 4),
                    "t_wall": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False) + "\n")
                n += 1

                if n % 50 == 0:
                    mf.flush()
                    log.info(f"  已录 {n} 帧 ({t - t0:.1f}s)")
                # 停止条件
                if (t - t0) >= args.duration:
                    log.info("到达 --duration,停。"); break
                if n >= args.max_frames:
                    log.info("到达 --max-frames,停。"); break
                if (t - last_disk_check) > 10:
                    last_disk_check = t
                    if shutil.disk_usage(out).free / 1e9 < args.min_free_gb:
                        log.warning("磁盘低于阈值,停。"); break
    except KeyboardInterrupt:
        log.info("Ctrl-C,停。")
    finally:
        try:
            camera.stop(); camera.release()
        except Exception:
            pass

    dt = time.perf_counter() - t0
    eff_fps = n / dt if dt > 0 else 0
    log.info(f"完成:{n} 帧 / {dt:.1f}s,实测有效 {eff_fps:.1f} fps(目标 {args.fps}).")
    log.info(f"帧目录: {frames_dir.resolve()}")
    log.info(f"manifest: {manifest_path.resolve()}")
    if eff_fps < args.fps * 0.6:
        log.warning(f"⚠️ 实测 fps 明显低于目标 —— 可能写盘/抓取瓶颈,这本身就是 T125 吞吐的线索。")
    log.info("R-3 提醒:帧含其他玩家昵称,标注完成后建议删除本目录。")


if __name__ == "__main__":
    main()

# ────────────────────────────────────────────────────────────────────
# [WIN 验证 CHECKLIST] —— 首次在 Win 端逐条确认(作者无法在 Linux 验)
#   1. pip install dxcam pygetwindow opencv-python numpy 成功
#   2. 开 WePoker,跑 --window-title "WePoker"(标题对不上就换关键词 / 用 --region)
#      → 看日志"命中窗口"的坐标是否正确框住牌桌
#   3. 黑屏自检:若报"近乎全黑" → 依次试 --output-idx 0 / 1 /…(多屏几乎必踩,§7.3)
#   4. 录 10-20 秒 → 打开 data/recordings/<stamp>/frames/ 抽几张 PNG 看:
#      - 画面完整、颜色正常(不偏蓝=BGR 对)、牌桌都在框里、聊天区是否需要 --region 排除
#   5. 看结尾"实测有效 fps":若远低于目标 → 写盘是瓶颈(给 T125 一个早信号)
#   6. 确认 manifest.jsonl 每行有 i/file/t_mono/t_wall(后面靠 t_wall 跟 DB 手牌对齐)
# 全过 → 回报,我再据此(实测 fps / 文件大小)调默认值并提交。
# ────────────────────────────────────────────────────────────────────
