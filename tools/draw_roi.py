"""tools/draw_roi.py — 把指定 ROI 框画在一张录像帧上(眼验框对不对/框住目标没)。

用法(Win):
  # 看 +xx 框(挑一张有人刚赢、+xx 在显示的帧最直观;没有也能看框相对头/stack 的位置)
  python tools\\draw_roi.py --frame "data\\recordings\\<ts>\\frames\\f_000123.png" --profile party_poker_8 --regions win_amount
  # 想同时看参考(win_amount 在 stack 上方 138px):
  python tools\\draw_roi.py --frame "...f_000123.png" --regions win_amount,stack
存 tools\\output\\draw_roi\\<frame>_annot.png,打开看每座框住没住目标。
"""
import argparse
import json
import os
import sys

import cv2

REGION_COLORS = {  # BGR
    "win_amount": (0, 255, 128),  # 亮绿
    "stack": (255, 200, 0),       # 青
    "fold_area": (0, 165, 255),   # 橙
    "id": (255, 0, 255),          # 品红
    "amount": (0, 255, 255),      # 黄
    "action": (200, 200, 200),
    "hand_type": (128, 0, 255),
    "allin_star_area": (0, 255, 0),   # 亮绿 = 你框的星星区(要看的)
    "cards": (0, 0, 255),             # 红 = 摊牌牌区(必须避开它)
    "card_marker": (255, 255, 0),     # 青 = 牌背(参照)
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--regions", default="win_amount", help="逗号分隔,如 win_amount 或 win_amount,stack")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    prof = json.load(open(os.path.join("rois", f"{args.profile}.json"), encoding="utf-8"))
    img = cv2.imread(args.frame)
    if img is None:
        print(f"读不到帧:{args.frame}"); sys.exit(2)
    regions = [r.strip() for r in args.regions.split(",") if r.strip()]

    n = 0
    for s in prof["seats"]:
        si = s["seat_index"]
        for region in regions:
            box = s.get(region)
            if not box:
                continue
            l, t, w, h = box
            color = REGION_COLORS.get(region, (0, 255, 0))
            cv2.rectangle(img, (l, t), (l + w, t + h), color, 2)
            cv2.putText(img, f"s{si}", (l, max(t - 4, 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
            n += 1

    out = args.out or os.path.join("tools", "output", "draw_roi",
                                   os.path.basename(args.frame).replace(".png", "_annot.png"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cv2.imwrite(out, img)
    print(f"✅ 画了 {n} 个框({','.join(regions)})→ {out}")
    print("   打开看:每座框有没有【完整罩住】+xx 显示位;偏了就告诉我偏多少(8 座统一调)。")


if __name__ == "__main__":
    main()
