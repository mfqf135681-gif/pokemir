r"""tools/build_pot_label_phash.py — 从信源验证 label session 建"总底池"text-shape phash 参考。

复用 pipeline.action_phash.text_shape_hash(单一实现,build/live 同源防漂移,同 #240)。
"总底池"帧(labeled.jsonl 里 raw_text 含"总"+"池")→ hash → 存 rois/pot_label_phash_<profile>.json
(ActionPhashReader 格式,live 直接 ActionPhashReader.load 读)。
报 intra(总底池互相)/ inter(总底池 vs 数字)hamming 分离度 → 据此定 match_threshold(自动建议)。

用法:
  python tools/build_pot_label_phash.py data/label_sessions/pot_size_<ts>
  python tools/build_pot_label_phash.py <session> --out rois/pot_label_phash_party_poker_8.json --grid 8
"""
import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import cv2  # noqa: E402
from pipeline.action_phash import text_shape_hash, hamming  # noqa: E402  单一实现


def main():
    ap = argparse.ArgumentParser(description="从 label session 建'总底池'phash 参考")
    ap.add_argument("session", help="信源验证 label session 目录(含 labeled.jsonl + crop)")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--out", default="", help="省略 → rois/pot_label_phash_<profile>.json")
    ap.add_argument("--grid", type=int, default=8, help="phash 网格(grid²位;build/live 须一致)")
    args = ap.parse_args()

    sess = Path(args.session)
    labeled = [json.loads(l) for l in open(sess / "labeled.jsonl", encoding="utf-8") if l.strip()]

    label_hashes, num_hashes = [], []
    for rec in labeled:
        raw = rec.get("raw_text") or ""
        crop = cv2.imread(str(sess / rec["crop"]))
        if crop is None:
            continue
        h = text_shape_hash(crop, grid=args.grid)
        if not h:
            continue
        if "总" in raw and "池" in raw:          # 结算帧标记(raw_text 由采集时 EasyOCR 旁路留)
            label_hashes.append((rec["crop"], h))
        elif str(rec.get("truth", "")).strip().isdigit():
            num_hashes.append(h)

    if not label_hashes:
        sys.exit("✗ 没找到'总底池'帧(labeled.jsonl 里 raw_text 含 总+池)。"
                 "可能这个 session 没采到结算帧,或采集时已砍 EasyOCR(raw_text 全空)→ 换昨天那份带 raw_text 的。")

    lh = [h for _, h in label_hashes]
    intra = [hamming(a, b) for i, a in enumerate(lh) for b in lh[i + 1:]]
    inter = [min(hamming(a, b) for b in lh) for a in num_hashes]
    intra_max = max(intra) if intra else 0
    inter_min = min(inter) if inter else 64
    print(f"总底池样本 {len(lh)} 张 {[c for c, _ in label_hashes]};数字样本 {len(num_hashes)} 张")
    print(f"intra(总底池互相)hamming: {sorted(intra)}  → max={intra_max}")
    print(f"inter(数字→最近总底池)hamming: min={inter_min}  (要 > intra_max 才分得开)")
    if inter_min <= intra_max:
        print("  ⚠️ inter ≤ intra:总底池和数字 phash 分不开!grid 调大试试,或框收窄只罩'总底池'字。")
    sug = (intra_max + inter_min) // 2
    print(f"→ 建议 match_threshold ≈ {sug}(intra_max {intra_max} < 此 < inter_min {inter_min})")

    out = args.out or str(Path(_ROOT) / "rois" / f"pot_label_phash_{args.profile}.json")
    json.dump({"refs": {"总底池": lh}, "match_threshold": int(sug), "grid": args.grid,
               "sat_th": 60, "val_th": 100, "margin": 0, "first_char": False},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"✅ 参考已存 {out}(live 由 ActionPhashReader.load 读;先 shadow 验,再接收手/摊牌窗)")


if __name__ == "__main__":
    main()
