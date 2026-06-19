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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--from-image")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--interval", type=float, default=0.7)
    ap.add_argument("--th", type=int, default=8)
    a = ap.parse_args()
    if not a.live and not a.from_image:
        ap.error("给 --from-image <帧> 或 --live")
    prof = json.loads((Path("rois") / f"{a.profile}.json").read_text(encoding="utf-8"))
    if a.live:
        run_live(prof, a.th, a.samples, a.interval)
    else:
        run_from_image(prof, a.from_image, a.th)


if __name__ == "__main__":
    main()
