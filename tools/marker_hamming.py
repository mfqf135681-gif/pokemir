"""tools/marker_hamming.py — 每座 card_marker hamming-to-ref(验重框/参考是否分得开)。

两种来源:
  --from-image <帧>  : 对单张 PNG 跑(harness 路径,_avg_hash=BGR)。⚠️ 拿 ref 的源帧测=循环验证,无意义;必须用【非源帧】。
  --live             : 直接抓实时屏幕(live 路径:ScreenCapturer 找窗口 + mss grab + BGRA 算法),
                       多次采样打印,可在某座在手/弃牌时实时看 hamming。绕开"没存录像帧"+循环验证两个坑。

判读:在手座应 ≤th;弃/空座 >th(bimodal)。某座占着却恒 >th=该座 ref/框做坏(2026-06-19 seat_0=43 即此)。
⚠️ Win-only(cv2 + mss + win32 窗口)。从项目根目录跑。

用法:
  python tools/marker_hamming.py --from-image data\\recordings\\<场>\\frames\\f_XXX.png
  python tools/marker_hamming.py --live                         # 抓 live,默认采 20 次每 0.7s
  python tools/marker_hamming.py --live --samples 40 --interval 0.5
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 让 import replay_reconstruct 生效
from replay_reconstruct import _avg_hash, _hamming  # noqa: E402


def _avg_hash_live(crop):
    """BGRA-aware 8x8 avg-hash(忠实复制 orchestrator._avg_hash_int:mss 给 4 通道,
    必须 BGRA2GRAY;与 _avg_hash 的 BGR2GRAY 灰度权重一致,只是适配通道数)。"""
    import cv2
    if crop is None or getattr(crop, "size", 0) == 0:
        return 0
    code = cv2.COLOR_BGRA2GRAY if (crop.ndim == 3 and crop.shape[2] == 4) else cv2.COLOR_BGR2GRAY
    g = cv2.resize(cv2.cvtColor(crop, code), (8, 8))
    m = float(g.mean())
    bits = 0
    for v in g.flatten():
        bits = (bits << 1) | (1 if float(v) > m else 0)
    return bits


def _seats_with_marker(prof):
    out = []
    for s in prof.get("seats", []):
        cm, ref = s.get("card_marker"), s.get("card_marker_ref")
        if cm and ref is not None:
            out.append((s["seat_index"], cm, ref))
    return out


def run_from_image(prof, path, th):
    import cv2
    img = cv2.imread(path)
    if img is None:
        print(f"ERROR: 读不到图 {path}")
        return
    print(f"帧 {path}  (th={th})")
    for sidx, cm, ref in _seats_with_marker(prof):
        l, t, w, h = cm
        ham = _hamming(_avg_hash(img[t:t + h, l:l + w]), ref)
        print(f"  seat{sidx}: hamming={ham:>2}  {'✅在手' if ham <= th else '⬜不在手'}")


def run_live(prof, th, samples, interval):
    from capture.roi import ROIRegion
    from capture.screen import ScreenCapturer
    title = prof.get("window_title", "")
    cap = ScreenCapturer()
    if title and cap.find_window_by_title(title):
        print(f"跟踪窗口: {title!r}")
    else:
        print(f"⚠️ 未找到窗口 {title!r},回退 monitor 1(坐标可能不对)")
        cap.select_monitor(1)
    seats = _seats_with_marker(prof)
    if not seats:
        print("ERROR: profile 无 card_marker/ref 的座")
        return
    hdr = "  ".join(f"s{sidx}" for sidx, _, _ in seats)
    print(f"LIVE hamming(≤{th}=在手) 每 {interval}s 采一次,共 {samples} 次。在手座应低、弃/空应高。")
    print(f"      {hdr}   →在手座")
    for i in range(samples):
        cap.refresh_frame()  # 整窗抓一帧,各座从中切片=同一瞬间、与 live 同路径
        cells, active = [], []
        for sidx, cm, ref in seats:
            l, t, w, h = cm
            crop = cap.capture_roi(ROIRegion("cm", left=l, top=t, width=w, height=h))
            ham = _hamming(_avg_hash_live(crop), ref)
            cells.append(f"{ham:>2}")
            if ham <= th:
                active.append(sidx)
        cap.clear_frame()
        print(f"#{i:>2}  {'  '.join(cells)}   {sorted(active)}")
        if i < samples - 1:
            time.sleep(interval)


def run_dump(prof, th, outdir):
    """抓一帧 live → 全窗画上各座 card_marker 框(绿=在手/红=不匹配)+ 各座放大 crop 存盘。
    看 _overlay:某座框压在两红牌背上但 hamming 高=ref内容坏;框飘到头像/别处=框位坏。"""
    import cv2
    from capture.roi import ROIRegion  # noqa: F401  (确保模块可导)
    from capture.screen import ScreenCapturer
    title = prof.get("window_title", "")
    cap = ScreenCapturer()
    if title and cap.find_window_by_title(title):
        print(f"跟踪窗口: {title!r}")
    else:
        print(f"⚠️ 未找到窗口 {title!r},回退 monitor 1(坐标可能不对)")
        cap.select_monitor(1)
    cap.refresh_frame()
    frame = cap._frame
    if frame is None:
        print("ERROR: 抓帧失败")
        return
    bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR) if (frame.ndim == 3 and frame.shape[2] == 4) else frame.copy()
    os.makedirs(outdir, exist_ok=True)
    overlay = bgr.copy()
    for sidx, cm, ref in _seats_with_marker(prof):
        l, t, w, h = cm
        ham = _hamming(_avg_hash_live(frame[t:t + h, l:l + w]), ref)
        color = (0, 200, 0) if ham <= th else (0, 0, 255)  # BGR:绿/红
        cv2.rectangle(overlay, (l, t), (l + w, t + h), color, 1)
        cv2.putText(overlay, f"s{sidx}:{ham}", (l, max(8, t - 2)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        crop = bgr[t:t + h, l:l + w]
        if crop.size:
            up = cv2.resize(crop, (max(1, w) * 6, max(1, h) * 6), interpolation=cv2.INTER_NEAREST)
            cv2.imwrite(os.path.join(outdir, f"s{sidx}_ham{ham}.png"), up)
    cv2.imwrite(os.path.join(outdir, "_overlay.png"), overlay)
    print(f"已存 → {outdir}\\")
    print("  _overlay.png   = 全窗 + 各座 card_marker 框(绿=在手/红=不匹配,标了 hamming)")
    print("  s<座>_ham<值>.png = 各座框内容放大6倍")
    print("看点:seat_0 红框是否压在那两张红牌背上?")
    print("  压在牌背上但红(hamming高) → ref 内容坏(重录 ref);框飘到头像/缝隙 → 框位坏(重框)。对比 seat_1/2 绿框。")


def run_set_ref(profile_name, prof, seat, outdir):
    """单座 live 重录 card_marker_ref(只改该座,不碰其它座的好 ref)。
    抓一帧 live → 算该座 card_marker 框的 avg_hash → 写回 profile 该座 card_marker_ref。
    ⚠️ 跑时该座必须【在手(露两张牌背)】,否则录进背景=又坏。存 crop 供肉眼复核。"""
    import cv2
    from capture.roi import ROIRegion  # noqa: F401
    from capture.screen import ScreenCapturer
    seatrow = next((s for s in prof.get("seats", []) if s.get("seat_index") == seat), None)
    if not seatrow or not seatrow.get("card_marker"):
        print(f"ERROR: profile 无 seat{seat} 的 card_marker 框(先用 roi_config 重框)")
        return
    title = prof.get("window_title", "")
    cap = ScreenCapturer()
    if title and cap.find_window_by_title(title):
        print(f"跟踪窗口: {title!r}")
    else:
        print(f"⚠️ 未找到窗口 {title!r},回退 monitor 1")
        cap.select_monitor(1)
    cap.refresh_frame()
    frame = cap._frame
    if frame is None:
        print("ERROR: 抓帧失败")
        return
    l, t, w, h = seatrow["card_marker"]
    crop = frame[t:t + h, l:l + w]
    new_ref = _avg_hash_live(crop)
    old_ref = seatrow.get("card_marker_ref")
    # 写回 profile(只改该座该键,保留其它一切)
    path = Path("rois") / f"{profile_name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for s in data.get("seats", []):
        if s.get("seat_index") == seat:
            s["card_marker_ref"] = int(new_ref)
            break
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.makedirs(outdir, exist_ok=True)
    bgr = cv2.cvtColor(crop, cv2.COLOR_BGRA2BGR) if (crop.ndim == 3 and crop.shape[2] == 4) else crop
    cv2.imwrite(os.path.join(outdir, f"s{seat}_newref.png"),
                cv2.resize(bgr, (max(1, w) * 6, max(1, h) * 6), interpolation=cv2.INTER_NEAREST))
    print(f"✓ seat{seat} card_marker_ref: {old_ref} → {int(new_ref)}  已写 {path}")
    print(f"  录到的内容存 {outdir}\\s{seat}_newref.png —— 务必确认是【两张红牌背】!不是就重跑(确保该座在手)")
    print(f"  然后 --live 在后续几手验证:seat{seat} 在手时应 ≤8。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--from-image")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--dump", action="store_true", help="抓一帧 live,画框+存各座 crop(看 ROI 位置)")
    ap.add_argument("--set-ref", type=int, default=None, metavar="SEAT",
                    help="单座 live 重录 card_marker_ref(该座必须在手;只改该座不碰其它)")
    ap.add_argument("--out", default="out/marker_check")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--interval", type=float, default=0.7)
    ap.add_argument("--th", type=int, default=8)
    a = ap.parse_args()
    if not a.live and not a.dump and a.set_ref is None and not a.from_image:
        ap.error("给 --from-image <帧> 或 --live 或 --dump 或 --set-ref <座>")
    prof = json.loads((Path("rois") / f"{a.profile}.json").read_text(encoding="utf-8"))
    if a.set_ref is not None:
        run_set_ref(a.profile, prof, a.set_ref, a.out)
    elif a.dump:
        run_dump(prof, a.th, a.out)
    elif a.live:
        run_live(prof, a.th, a.samples, a.interval)
    else:
        run_from_image(prof, a.from_image, a.th)


if __name__ == "__main__":
    main()
