r"""tools/verify_frame_capture.py — A步3 尺子:验 FRAME_CAPTURE 切片 == 逐区 grab(字节级)。

FRAME_CAPTURE(整帧抓一次 + 内存切片)要替"逐ROI单独grab"省时,前提是**读到的像素完全一致**。
本工具用【真窗口(同 live 初始化)+ profile 全部真 ROI】逐个比对:
  slice(整帧切片) vs grab(逐区独立抓),字节级 maxdiff。
**区分坐标bug vs 动画**(关键):
  - 同一 ROI 连抓两次整帧、各切一片(slice_A vs slice_B)→ 这俩差=该区在【动画】(timer/闪烁);
  - slice vs grab 差,但 slice_A==slice_B(区静止)→ 真【坐标基准 bug】(切错地方)。
判读:静止区 slice==grab 全部 0 → FRAME_CAPTURE 安全可开;静止区有非 0 → 坐标 bug,别开。

⚠️ Win-only(mss + 真窗口)。务必在【牌局静止/暂停】时跑(动画区会"假不一致",已被 slice_A/B 标出)。
用法(Win):
  python tools\verify_frame_capture.py --profile party_poker_8
  python tools\verify_frame_capture.py --profile party_poker_8 --window-title "WePoker"
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capture.screen import ROIRegion, ScreenCapturer  # noqa: E402


def collect_rois(prof):
    """从 profile 收集全部 ROI(每座每个 4-元 box 键 + 顶层 4-元 box 键)→ [(name, ROIRegion)]。"""
    out = []
    SKIP = {"seat_index"}

    def add(name, box):
        if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            l, t, w, h = box
            if w >= 1 and h >= 1:
                out.append((name, ROIRegion(name, int(l), int(t), int(w), int(h))))

    for s in prof.get("seats", []):
        si = s.get("seat_index", "?")
        for k, v in s.items():
            if k not in SKIP:
                add(f"s{si}.{k}", v)
    for k, v in prof.items():  # 顶层(hero_card_1/2、community 等若是 4-元 box)
        if k not in ("seats",):
            add(k, v)
    return out


def maxdiff(a, b):
    if a is None or b is None or a.shape != b.shape:
        return -1  # 形状不一致 = 严重
    return int(np.abs(a.astype(np.int32) - b.astype(np.int32)).max())


def main():
    ap = argparse.ArgumentParser(description="验 FRAME_CAPTURE 切片==逐区grab(字节级)")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--roi-dir", default="./rois")
    ap.add_argument("--window-title", default=None, help="覆盖 profile 的 window_title")
    ap.add_argument("--diff-th", type=int, default=0, help="判'不一致'的 maxdiff 阈(默认 0=字节级全等)")
    args = ap.parse_args()

    prof_path = os.path.join(args.roi_dir, f"{args.profile}.json")
    prof = json.load(open(prof_path, encoding="utf-8"))
    rois = collect_rois(prof)
    print(f"profile {args.profile}: {len(rois)} 个 ROI")

    cap = ScreenCapturer()
    title = args.window_title or prof.get("window_title", "")
    if title and cap.find_window_by_title(title):
        wr = cap._window_rect
        print(f"✅ 找到窗口 {title!r} @ ({wr['left']},{wr['top']}) {wr['width']}x{wr['height']}（窗口基准）")
    else:
        cap.select_monitor(1)
        print(f"⚠️ 未找到窗口 {title!r} → 回退 monitor 1（注意:这测的是 monitor 基准,非 live 窗口基准!）")

    # 每个 ROI:slice_A(整帧切片) / grab(逐区抓) / slice_B(再抓整帧切片,查动画)
    coord_bug, animating, matched, shape_err = [], [], [], []
    for name, roi in rois:
        cap.refresh_frame()
        slice_a = cap.capture_roi(roi)
        cap.clear_frame()
        grab = cap.capture_roi(roi)          # 逐区独立 grab
        cap.refresh_frame()
        slice_b = cap.capture_roi(roi)
        cap.clear_frame()

        d_sg = maxdiff(slice_a, grab)         # 切片 vs 逐区grab
        d_static = maxdiff(slice_a, slice_b)  # 两次整帧切片 = 动画指示
        if d_sg == -1:
            shape_err.append((name, slice_a.shape if slice_a is not None else None, grab.shape if grab is not None else None))
        elif d_sg <= args.diff_th:
            matched.append(name)
        elif d_static > args.diff_th:
            animating.append((name, d_sg, d_static))   # 区在动 → 不一致不可判
        else:
            coord_bug.append((name, d_sg))              # 区静止却 slice≠grab → 真坐标 bug

    print(f"\n{'='*60}")
    print(f"✅ 字节级一致(slice==grab): {len(matched)}/{len(rois)}")
    if animating:
        print(f"🟡 动画区(slice_A≠slice_B,本次抓拍时在变,不可判,建议静止重跑): {len(animating)}")
        for n, dsg, dst in animating[:12]:
            print(f"     {n}: slice-grab差={dsg} 动画差={dst}")
    if shape_err:
        print(f"🔴 形状不一致(越界/裁剪 bug): {len(shape_err)}")
        for n, sa, gb in shape_err[:12]:
            print(f"     {n}: slice{sa} vs grab{gb}")
    if coord_bug:
        print(f"🔴 坐标基准 bug(区静止却 slice≠grab,切错地方!): {len(coord_bug)}")
        for n, d in coord_bug[:20]:
            print(f"     {n}: maxdiff={d}")

    print(f"\n{'='*60}")
    if not coord_bug and not shape_err and not animating:
        print("判决:全部字节级一致 → FRAME_CAPTURE 安全,可开(A步3 通过)。")
    elif not coord_bug and not shape_err:
        print("判决:静止区全一致,只有动画区不可判 → 在【牌局完全静止】时重跑确认动画区也一致,再开。")
    else:
        print("判决:有坐标bug/形状错 → 【别开 FRAME_CAPTURE】,先修坐标基准。")


if __name__ == "__main__":
    main()
