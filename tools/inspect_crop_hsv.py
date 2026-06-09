"""tools/inspect_crop_hsv.py — 量单张 crop 的像素 HSV 分布(数据定阈,不靠肉眼看图)。

给 allin_star_area 的 dump 小图(正样本=有星星 / 负样本=纯桌布),用 cv2 量【实测】HSV:
  ① 整图 H/S/V 百分位;
  ② 按亮度分两簇(亮像素=星星候选 / 暗像素=桌布),各报 H/S/V → 看星星真实颜色;
  ③ 现行黄规则(H[18,38] S>40 V>120)到底命中几个像素 → 解释为何只数到个位;
  ④ 几条候选规则的命中数对比(白 / 亮非绿 / 宽黄)→ 给"能把星星和桌布分开"的阈值。

用法(Win 或本机 venv,cv2 同 live):
  python tools/inspect_crop_hsv.py 正样本.png [更多.png ...]
  python tools/inspect_crop_hsv.py --star s2_hi_y5_xxx.png --felt s2_lo_y0_xxx.png  # 正负对照
多张正样本 + 多张负样本一起给,末尾打印【星星 vs 桌布】分离阈值建议。
"""
import argparse
import glob
import sys

import cv2
import numpy as np


def pct(a, ps=(0, 5, 25, 50, 75, 95, 100)):
    if a.size == 0:
        return "(空)"
    return " ".join(f"p{p}={int(np.percentile(a, p))}" for p in ps)


def load_hsv(path):
    img = cv2.imread(path)
    if img is None:
        return None, None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return img, hsv


def rule_counts(hsv):
    """几条候选规则各命中多少像素(占比)。"""
    h, s, v = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    n = hsv.shape[0] * hsv.shape[1]
    rules = {
        "现行黄 H[18,38]S>40V>120": (h >= 18) & (h <= 38) & (s > 40) & (v > 120),
        "宽黄   H[15,45]S>30V>100": (h >= 15) & (h <= 45) & (s > 30) & (v > 100),
        "白     S<60 V>180":        (s < 60) & (v > 180),
        "白宽   S<80 V>150":        (s < 80) & (v > 150),
        "亮     V>180(任意色)":      v > 180,
        "亮非绿 V>150 & not(S>60,H[35,90])": (v > 150) & ~((s > 60) & (h >= 35) & (h <= 90)),
    }
    return n, {k: (int(m.sum()), 100.0 * int(m.sum()) / max(n, 1)) for k, m in rules.items()}


def analyze(path, star_pixels_acc=None, felt_pixels_acc=None):
    img, hsv = load_hsv(path)
    name = path.split("/")[-1].split("\\")[-1]
    if img is None:
        print(f"  读不到:{path}")
        return
    h, w = hsv.shape[:2]
    H, S, V = hsv[..., 0].ravel(), hsv[..., 1].ravel(), hsv[..., 2].ravel()
    print(f"\n【{name}】 {w}×{h} = {w*h}px")
    print(f"  全图: H[{pct(H)}]")
    print(f"        S[{pct(S)}]")
    print(f"        V[{pct(V)}]")
    # 按亮度分簇:V 最高 20% = 星星候选;最低 50% = 桌布
    vth_hi = np.percentile(V, 80)
    vth_lo = np.percentile(V, 50)
    bright = V >= max(vth_hi, 120)   # 至少 120 才算"亮"
    dark = V <= vth_lo
    nb = int(bright.sum())
    if nb > 0:
        print(f"  亮簇(V≥{int(max(vth_hi,120))}, {nb}px, 星星候选): "
              f"H[{pct(H[bright])}] S[{pct(S[bright])}] V[{pct(V[bright])}]")
        if star_pixels_acc is not None:
            star_pixels_acc.append(np.stack([H[bright], S[bright], V[bright]], axis=1))
    else:
        print(f"  亮簇: 无 V≥120 像素(全暗→可能没罩住星星 / 是纯桌布)")
    print(f"  暗簇(V≤{int(vth_lo)}, 桌布基线): "
          f"H[{pct(H[dark])}] S[{pct(S[dark])}] V[{pct(V[dark])}]")
    if felt_pixels_acc is not None:
        felt_pixels_acc.append(np.stack([H[dark], S[dark], V[dark]], axis=1))
    # 规则命中
    n, rc = rule_counts(hsv)
    print("  规则命中:")
    for k, (cnt, p) in rc.items():
        print(f"    {k:32s} {cnt:5d}px ({p:5.2f}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="crop 图(可多张/可通配)")
    ap.add_argument("--star", nargs="*", default=[], help="正样本(有星星)")
    ap.add_argument("--felt", nargs="*", default=[], help="负样本(纯桌布)")
    args = ap.parse_args()

    def expand(xs):
        out = []
        for x in xs:
            g = glob.glob(x)
            out.extend(g if g else [x])
        return out

    star_paths = expand(args.star)
    felt_paths = expand(args.felt)
    plain = expand(args.paths)
    if not (star_paths or felt_paths or plain):
        print("给我图:python tools/inspect_crop_hsv.py 图.png  或  --star ... --felt ...")
        sys.exit(2)

    star_acc, felt_acc = [], []
    if plain:
        print("=" * 60 + "\n未分类样本")
        for p in plain:
            analyze(p, star_acc, felt_acc)
    if star_paths:
        print("=" * 60 + "\n正样本(星星)")
        for p in star_paths:
            analyze(p, star_acc, None)
    if felt_paths:
        print("=" * 60 + "\n负样本(桌布)")
        for p in felt_paths:
            analyze(p, None, felt_acc)

    # 分离阈值建议:正样本亮簇 vs 负样本桌布,找 H/S/V 上能分开的边界
    if star_acc and felt_acc:
        star = np.concatenate(star_acc)
        felt = np.concatenate(felt_acc)
        print("\n" + "=" * 60)
        print(f"【分离分析】星星亮像素 n={len(star)}  vs  桌布像素 n={len(felt)}")
        for i, ch in enumerate("HSV"):
            sp = star[:, i]
            fp = felt[:, i]
            print(f"  {ch}: 星[{pct(sp)}]")
            print(f"     桌[{pct(fp)}]")
        print("  → 找一条让'星 p25 一侧'与'桌 p75 一侧'分开的阈值即可(看上面重叠不重叠)。")


if __name__ == "__main__":
    main()
