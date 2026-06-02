"""tools/frames_to_video.py — 把录制的帧序列拼成 mp4,供人工标注时拖进度条看(T126 步骤③辅助)

标注 900 张 PNG 一张张翻太痛苦 → 拼成视频,在播放器里拖动/暂停/慢放,逐手记动作快得多。
(视频仅供人眼看;A/B 回放/识别仍用原始 PNG,不用这个有损视频。)

依赖:opencv-python(已装)
用法(Win PowerShell):
  .\\.venv\\Scripts\\python.exe tools\\frames_to_video.py --session data\\recordings\\20260602_090828
  # 慢放(2fps 输出,动作看得更清):
  .\\.venv\\Scripts\\python.exe tools\\frames_to_video.py --session data\\recordings\\... --fps 2
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("frames_to_video")


def main():
    ap = argparse.ArgumentParser(description="帧序列 → mp4(标注辅助,T126)")
    ap.add_argument("--session", type=str, help="录制 session 目录(含 frames/);输出 replay.mp4 到此")
    ap.add_argument("--frames", type=str, help="直接指定帧目录(覆盖 --session)")
    ap.add_argument("--out", type=str, default=None, help="输出 mp4 路径(默认 <session>/replay.mp4)")
    ap.add_argument("--fps", type=float, default=5.0, help="输出帧率(默认 5=原速;调小=慢放看得清)")
    args = ap.parse_args()

    try:
        import cv2
    except ImportError:
        log.error("缺 opencv-python:pip install opencv-python"); sys.exit(2)

    if args.frames:
        frames_dir = Path(args.frames)
        out_path = Path(args.out) if args.out else frames_dir.parent / "replay.mp4"
    elif args.session:
        frames_dir = Path(args.session) / "frames"
        out_path = Path(args.out) if args.out else Path(args.session) / "replay.mp4"
    else:
        ap.error("给 --session <录制目录> 或 --frames <帧目录>")

    if not frames_dir.is_dir():
        log.error(f"帧目录不存在: {frames_dir}"); sys.exit(2)

    frames = sorted(frames_dir.glob("f_*.png")) + sorted(frames_dir.glob("f_*.jpg"))
    frames = sorted(frames, key=lambda p: p.name)
    if not frames:
        log.error(f"{frames_dir} 里没有 f_*.png/jpg"); sys.exit(2)
    log.info(f"{len(frames)} 帧 → {out_path}  @ {args.fps}fps")

    first = cv2.imread(str(frames[0]))
    if first is None:
        log.error(f"读不出首帧 {frames[0]}"); sys.exit(2)
    h, w = first.shape[:2]
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (w, h))
    if not writer.isOpened():
        log.error("VideoWriter 打不开(缺编解码器?)。试装 opencv 或换 --out 后缀。"); sys.exit(3)

    n = 0
    for fp in frames:
        img = cv2.imread(str(fp))
        if img is None:
            log.warning(f"跳过读不出的帧 {fp.name}"); continue
        if img.shape[:2] != (h, w):
            img = cv2.resize(img, (w, h))
        writer.write(img)
        n += 1
        if n % 200 == 0:
            log.info(f"  {n}/{len(frames)}")
    writer.release()
    secs = n / args.fps if args.fps else 0
    log.info(f"完成:{n} 帧 → {out_path}({secs:.0f}s 视频)。用播放器(VLC/WMP)拖进度条标注。")


if __name__ == "__main__":
    main()
