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


def load_frame_times(manifest_path):
    """读 manifest.jsonl → {帧文件名: t_mono}。纯逻辑(无 cv2),Linux 可单测。
    manifest 首行是 _meta,其余每行 {i, file, t_mono, ...}。无文件/损坏 → 返回 {}。"""
    import json
    times = {}
    p = Path(manifest_path)
    if not p.is_file():
        return times
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except (ValueError, TypeError):
            continue
        if d.get("_meta"):
            continue
        f, t = d.get("file"), d.get("t_mono")
        if f is not None and t is not None:
            times[f] = float(t)
    return times


def burn_label(cv2, img, text):
    """左上角黑底白字烧文字(真实时间戳+帧名)→ 慢放/任意输出 fps 下都读得到真实时间,
    根治"视频秒≠真实秒"对齐问题。Win-only(cv2)。"""
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
    cv2.rectangle(img, (0, 0), (tw + 14, th + 16), (0, 0, 0), -1)
    cv2.putText(img, text, (7, th + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)


def main():
    ap = argparse.ArgumentParser(description="帧序列 → mp4(标注辅助,T126)")
    ap.add_argument("--session", type=str, help="录制 session 目录(含 frames/);输出 replay.mp4 到此")
    ap.add_argument("--frames", type=str, help="直接指定帧目录(覆盖 --session)")
    ap.add_argument("--out", type=str, default=None, help="输出 mp4 路径(默认 <session>/replay.mp4)")
    ap.add_argument("--fps", type=float, default=5.0, help="输出帧率(默认 5;调小=慢放看得清。烧时间戳后此值随便调,不影响对齐)")
    ap.add_argument("--no-burn", action="store_true",
                    help="不烧时间戳(默认烧:每帧角上印真实 t_mono+帧名 → 慢放也读得到真实秒,治帧率不一致对齐)")
    ap.add_argument("--manifest", type=str, default=None, help="manifest.jsonl 路径(默认 <session>/manifest.jsonl)")
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

    # 烧时间戳:读 manifest 拿每帧真实 t_mono(无则告警、退化为不烧)
    times = {}
    if not args.no_burn:
        mani = Path(args.manifest) if args.manifest else frames_dir.parent / "manifest.jsonl"
        times = load_frame_times(mani)
        if times:
            log.info(f"烧时间戳:manifest {mani.name} 读到 {len(times)} 帧 t_mono")
        else:
            log.warning(f"未读到 manifest({mani})→ 不烧时间戳(对齐请用 --no-burn 自知,或补 manifest)")
    log.info(f"{len(frames)} 帧 → {out_path}  @ {args.fps}fps  烧时间戳={'是' if times else '否'}")

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
        if times:  # 烧真实时间戳 + 帧名(治帧率不一致对齐)
            t = times.get(fp.name)
            burn_label(cv2, img, f"t={t:.1f}s  {fp.stem}" if t is not None else fp.stem)
        writer.write(img)
        n += 1
        if n % 200 == 0:
            log.info(f"  {n}/{len(frames)}")
    writer.release()
    secs = n / args.fps if args.fps else 0
    log.info(f"完成:{n} 帧 → {out_path}({secs:.0f}s 视频)。用播放器(VLC/WMP)拖进度条标注。")


if __name__ == "__main__":
    main()
