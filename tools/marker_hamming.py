"""tools/marker_hamming.py — 单帧每座 card_marker hamming-to-ref(验重框/参考是否分得开)。

复用 replay_reconstruct 的 _avg_hash/_hamming(单一实现,防 harness/live 漂移)。
对【在手帧】跑应全 ≤th;对【部分弃牌/手间帧】跑,弃/空座应明显 >th(bimodal)。
⚠️ Win-only(cv2)。从项目根目录跑。

用法:
  python tools/marker_hamming.py --from-image data\\recordings\\<场>\\frames\\f_XXX.png
  python tools/marker_hamming.py --profile party_poker_8 --from-image <帧> --th 8
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 让 import replay_reconstruct 生效
from replay_reconstruct import _avg_hash, _hamming  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--from-image", required=True)
    ap.add_argument("--th", type=int, default=8)
    a = ap.parse_args()
    import cv2
    img = cv2.imread(a.from_image)
    if img is None:
        print(f"ERROR: 读不到图 {a.from_image}")
        return
    prof = json.loads((Path("rois") / f"{a.profile}.json").read_text(encoding="utf-8"))
    print(f"帧 {a.from_image}  (th={a.th})")
    for s in prof.get("seats", []):
        cm, ref = s.get("card_marker"), s.get("card_marker_ref")
        if not cm or ref is None:
            continue
        l, t, w, h = cm
        ham = _hamming(_avg_hash(img[t:t + h, l:l + w]), ref)
        print(f"  seat{s['seat_index']}: hamming={ham:>2}  {'✅在手' if ham <= a.th else '⬜不在手'}")


if __name__ == "__main__":
    main()
