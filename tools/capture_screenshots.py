r"""tools/capture_screenshots.py — 无损截屏(按空格依次存 PNG),采集动作参考用。

找 pipeline 同一窗口(profile 的 window_title,ROI 坐标对齐)→ 整窗 → PNG(无损)存
data/recordings/<时间戳>/frames/f_NNNNNN.png(与现有录像段平行,可直接喂 build_action_refs/auto_collect)。

可选 --labels "座:动作,…"(你的缺座清单,采集顺序):每按空格按清单下一项命名 f_NNNNNN_s{座}_{动作}.png,
退出时打印【直接可粘的 --append 锚串】,免你手动映射帧号↔动作。

键:空格=截当前窗口存盘并推进清单;b=撤销上一张(删文件+退回清单);q/ESC=退出并打印锚串。
⚠️ Windows 专用(msvcrt 读键)。用法:
  python tools\capture_screenshots.py --profile party_poker_8 --labels "1:bet,3:bet,4:bet,1:call,3:call,5:call,7:call,2:raise,3:raise,4:raise"
"""
import argparse
import json
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from capture.screen import ScreenCapturer  # noqa: E402

VALID = {"check", "raise", "call", "bet", "fold"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--labels", default="", help="逗号分隔 座:动作 采集顺序(如 1:bet,3:call,…);省略则只顺序编号")
    ap.add_argument("--out-root", default=os.path.join("data", "recordings"))
    args = ap.parse_args()

    import msvcrt  # Windows 单键读取

    labels = []
    for tok in args.labels.split(",") if args.labels else []:
        tok = tok.strip()
        if not tok:
            continue
        seat, act = tok.split(":")
        if act not in VALID:
            print(f"动作 {act!r} 非法(应 {VALID})"); sys.exit(2)
        labels.append((int(seat), act))

    # 窗口标题来自 profile(对齐 ROI 坐标)
    prof = json.load(open(os.path.join("rois", f"{args.profile}.json"), encoding="utf-8"))
    wt = prof.get("window_title", "")
    cap = ScreenCapturer()
    if wt and cap.find_window_by_title(wt):
        print(f"✅ 跟踪窗口: {wt!r}")
    else:
        cap.select_monitor(1)
        print(f"⚠️ 窗口 {wt!r} 没找到 → 回退显示器 1(ROI 坐标可能错位)")

    ts = time.strftime("%Y%m%d_%H%M%S")
    outdir = os.path.join(args.out_root, ts, "frames")
    os.makedirs(outdir, exist_ok=True)
    print(f"存到: {outdir}")
    if labels:
        print(f"清单 {len(labels)} 项: {', '.join(f's{s}:{a}' for s, a in labels)}")
    print("空格=截屏存盘  b=撤销上一张  q=退出\n")

    captures = []  # (filenum, seat|None, action|None)
    n = 1
    while True:
        if labels:
            nxt = labels[len(captures)] if len(captures) < len(labels) else None
            print(f"  下一张 → {('s%d:%s' % nxt) if nxt else '(清单已采完,继续=无标签)'}", end="\r")
        k = msvcrt.getch()
        if k == b" ":
            frame = cap.capture()
            if frame is None or frame.size == 0:
                print("\n  ⚠️ 抓空,跳过"); continue
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR) if frame.ndim == 3 and frame.shape[2] == 4 else frame
            lab = labels[len(captures)] if len(captures) < len(labels) else None
            suffix = f"_s{lab[0]}_{lab[1]}" if lab else ""
            name = f"f_{n:06d}{suffix}.png"
            cv2.imwrite(os.path.join(outdir, name), bgr)  # PNG 无损
            captures.append((n, lab[0] if lab else None, lab[1] if lab else None))
            print(f"\n  存 {name}  ({bgr.shape[1]}x{bgr.shape[0]})")
            n += 1
        elif k == b"b":
            if captures:
                fn, _, _ = captures.pop()
                for f in os.listdir(outdir):
                    if f.startswith(f"f_{fn:06d}"):
                        os.remove(os.path.join(outdir, f))
                print(f"\n  ↩ 撤销 f_{fn:06d}")
            else:
                print("\n  (无可撤销)")
        elif k in (b"q", b"\x1b"):
            break

    labeled = [(fn, s, a) for fn, s, a in captures if s is not None]
    print(f"\n\n共 {len(captures)} 张 → {outdir}")
    if labeled:
        anchors = ",".join(f"f_{fn:06d}:{s}:{a}" for fn, s, a in labeled)
        print("\n===== 直接粘这条建参考(全新重录;grid16 细分加注/下注)=====")
        print(f'python tools\\build_action_refs.py --frames-dir "{outdir}" '
              f'--profile {args.profile} --anchors "{anchors}" --grid 16 --threshold 40')
        print("(补缺座而非全建,则改 --append 并把现有录像目录一并加到 --frames-dir)")


if __name__ == "__main__":
    main()
