"""tools/field_hash.py — 任意 ROI 字段逐座 avg-hash + 跨座一致性(为抗漂配准选 per-seat 锚)。

用途:框好某字段(如把死字段 `timer` 槽当实验锚)后,跑此工具看 8 座 hash 是否一致——
  - 锚特征若【镜像对称】→ 8 座应全一致(组内 + 跨组 hamming 都低);
  - 锚特征若【左右不对称】→ 左型{0,1,2,3} 一簇、右型{4,5,6,7} 一簇,跨组高(镜像所致,非失败)。
判一个锚选得好不好:**组内 hamming 要低**(同型各座一致 = 该锚位置稳、不被遮);跨组高没关系。

复用 marker_hamming 的 _avg_hash_live / _hamming + ScreenCapturer(与 card_marker 同套 hash)。
⚠️ Win-only(cv2 + mss + 窗口)。从项目根目录跑。
用法:
  python tools/field_hash.py --field timer --live
  python tools/field_hash.py --field timer --from-image data\\recordings\\<场>\\frames\\f_XXXXXX.png
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from marker_hamming import _avg_hash_live, _hamming  # noqa: E402  与 card_marker 同套 hash
from replay_reconstruct import _avg_hash  # noqa: E402  BGR 版(--from-image 用)

# 镜像型分组(roi_derive 模型:左型 = 左列{1,2,3}+hero s0;右型 = 右列{5,6,7}+顶 s4)
LEFT_TYPE = (0, 1, 2, 3)
RIGHT_TYPE = (4, 5, 6, 7)


def _seat_boxes(prof, field):
    out = []
    for s in prof.get("seats", []):
        b = s.get(field)
        if b and isinstance(b, list) and len(b) == 4:
            out.append((s["seat_index"], b))
    return out


def _grab_live(prof):
    from capture.screen import ScreenCapturer
    title = prof.get("window_title", "")
    cap = ScreenCapturer()
    if title and cap.find_window_by_title(title):
        print(f"跟踪窗口: {title!r}")
    else:
        print(f"⚠️ 未找到窗口 {title!r},回退 monitor 1(坐标可能不对)")
        cap.select_monitor(1)
    cap.refresh_frame()
    return cap._frame


def _group_max(hashes, group):
    g = [s for s in group if s in hashes]
    if len(g) < 2:
        return None, g
    base = hashes[g[0]]
    return max(_hamming(hashes[s], base) for s in g[1:]), g


def report(hashes):
    seats = sorted(hashes)
    print("\n逐座 avg-hash(16 进制):")
    for s in seats:
        print(f"  seat_{s}: {hashes[s]:016x}")

    lmax, lg = _group_max(hashes, LEFT_TYPE)
    rmax, rg = _group_max(hashes, RIGHT_TYPE)
    print("\n镜像型组内一致性(越低越好,锚位置稳=组内同):")
    print(f"  左型 {lg} 组内 max-hamming(对 seat_{lg[0] if lg else '-'}): {lmax}")
    print(f"  右型 {rg} 组内 max-hamming(对 seat_{rg[0] if rg else '-'}): {rmax}")
    if lg and rg:
        cross = _hamming(hashes[lg[0]], hashes[rg[0]])
        print(f"  跨组 seat_{lg[0]} vs seat_{rg[0]}: {cross}  (高=锚左右不对称,镜像所致,正常)")

    print("\n两两 hamming 矩阵:")
    print("     " + " ".join(f"s{b}" for b in seats))
    for a in seats:
        print(f"  s{a} " + " ".join(f"{_hamming(hashes[a], hashes[b]):2d}" for b in seats))

    judge = []
    if lmax is not None:
        judge.append(f"左型{'一致✓' if lmax <= 4 else f'散✗({lmax})'}")
    if rmax is not None:
        judge.append(f"右型{'一致✓' if rmax <= 4 else f'散✗({rmax})'}")
    print("\n判读: " + " / ".join(judge) + "  (组内 ≤4 视作稳;散=该锚位置不稳或被遮,换特征)")


def _to_gray(frame):
    import cv2
    if frame.ndim == 3 and frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    if frame.ndim == 3:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame


def ncc_report(frame, boxes, margin):
    """NCC(归一化互相关)模式 = 真配准同款度量。两件事:
      ① 逐座【自匹配】:把该座锚 crop 当模板,在 box±margin 搜索窗里 matchTemplate →
         peak_corr(≈1 好)/ peak 偏移(自匹配应≈0,0)/ 2nd÷peak(低=峰尖锐唯一=可定位;高=有歧义,坏锚)。
      ② 跨座【一致性】:座两两 crop 的 NCC 相关(1.0=同图;CCOEFF 减均值,抗亮度)。"""
    import cv2
    import numpy as np
    g = _to_gray(frame)
    H, W = g.shape[:2]
    templ = {}
    for sidx, (l, t, w, h) in boxes:
        templ[sidx] = (g[t:t + h, l:l + w], (l, t, w, h))
    seats = sorted(templ)

    print("\n① 逐座自匹配(锚在搜索窗里好不好定位;margin=%d px):" % margin)
    print(f"  {'seat':5}{'peak_corr':>11}{'peak_off':>11}{'2nd/peak':>10}")
    for s in seats:
        tmpl, (l, t, w, h) = templ[s]
        if tmpl.size == 0:
            print(f"  s{s}: 空 crop"); continue
        x0, y0 = max(0, l - margin), max(0, t - margin)
        x1, y1 = min(W, l + w + margin), min(H, t + h + margin)
        region = g[y0:y1, x0:x1]
        if region.shape[0] < h or region.shape[1] < w:
            print(f"  s{s}: 搜索窗过小"); continue
        res = cv2.matchTemplate(region, tmpl, cv2.TM_CCOEFF_NORMED)
        _, maxv, _, maxloc = cv2.minMaxLoc(res)
        off = (maxloc[0] - (l - x0), maxloc[1] - (t - y0))
        res2 = res.copy()
        mx, my = maxloc
        res2[max(0, my - 2):my + 3, max(0, mx - 2):mx + 3] = -1.0
        second = float(res2.max()) if res2.size else -1.0
        ratio = (second / maxv) if maxv > 0 else 1.0
        verdict = "可定位✓" if (maxv >= 0.85 and ratio <= 0.6) else "弱✗"
        print(f"  s{s}{maxv:>11.3f}{str(off):>11}{ratio:>10.2f}  {verdict}")

    print("\n② 跨座一致性(两两 NCC 相关,1.00=同图,抗亮度;≥0.85 算一致):")
    print("       " + " ".join(f"s{b}  " for b in seats))
    for a_ in seats:
        ta = templ[a_][0]
        row = []
        for b in seats:
            tb = templ[b][0]
            if ta.shape != tb.shape or ta.size == 0:
                row.append(" n/a")
                continue
            r = cv2.matchTemplate(ta, tb, cv2.TM_CCOEFF_NORMED)
            row.append(f"{float(r.max()):.2f}")
        print(f"  s{a_} " + " ".join(f"{v:>4}" for v in row))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", required=True, help="要逐座 hash 的 ROI 字段名(如 timer / anchor / card_marker)")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--from-image")
    ap.add_argument("--ncc", action="store_true", help="用 NCC 模板匹配(配准同款,治 avg-hash 对小图标太粗)")
    ap.add_argument("--margin", type=int, default=10, help="--ncc 搜索窗边距(px,覆盖漂移幅度)")
    a = ap.parse_args()
    if not a.live and not a.from_image:
        ap.error("给 --live 或 --from-image <帧>")

    prof = json.load(open(os.path.join("rois", f"{a.profile}.json"), encoding="utf-8"))
    boxes = _seat_boxes(prof, a.field)
    if not boxes:
        print(f"ERROR: profile 里没有座位带字段 {a.field!r}(先框它)")
        return 2

    if a.from_image:
        import cv2
        frame = cv2.imread(a.from_image)
        if frame is None:
            print(f"ERROR: 读不到图 {a.from_image}")
            return 2
        fn = _avg_hash
    else:
        frame = _grab_live(prof)
        if frame is None:
            print("ERROR: 抓帧失败")
            return 2
        fn = _avg_hash_live

    print(f"字段={a.field!r}  座位数={len(boxes)}  源={'live' if a.live else a.from_image}  比法={'NCC模板匹配' if a.ncc else 'avg-hash'}")
    if a.ncc:
        ncc_report(frame, boxes, a.margin)
    else:
        hashes = {sidx: fn(frame[t:t + h, l:l + w]) for sidx, (l, t, w, h) in boxes}
        report(hashes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
