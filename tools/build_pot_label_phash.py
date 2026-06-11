r"""tools/build_pot_label_phash.py — 建"总底池"text-shape phash 参考(供 live shadow 检测结算帧)。

复用 pipeline.action_phash.text_shape_hash(单一实现,build/live 同源防漂移,同 #240)。
两种正样本来源:
  ① --from-frame PATH(可多次):录像整帧,按 profile 的 pot_size 框裁出 → hash(推荐:干净帧最稳);
  ② positional session(label session):取 raw_text 含"总"+"池"的帧 crop(昨天那种,可能糊)。
负样本(算 inter 分离度)= session 里的数字帧 crop(给了 session 才算)。
裁出的 crop 存 tools/output/pot_label/(眼验框对没,不污染根目录);ref 存 rois/pot_label_phash_<profile>.json。

用法:
  python tools/build_pot_label_phash.py --from-frame "data\recordings\20260602_170343\frames\f_000232.png" \
                                        "data\label_sessions\pot_size_20260610_184524"
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
    ap = argparse.ArgumentParser(description="建'总底池'phash 参考(--from-frame 裁干净帧 / session 取标注帧)")
    ap.add_argument("session", nargs="?", default="", help="label session 目录(取数字帧当负样本算分离度;可省)")
    ap.add_argument("--from-frame", action="append", default=[],
                    help="录像整帧路径(按 profile pot_size 裁);可多次 = 多张正样本(更厚)")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--out", default="", help="省略 → rois/pot_label_phash_<profile>.json")
    ap.add_argument("--crop-out", default="", help="裁图存放目录;省 → tools/output/pot_label")
    ap.add_argument("--grid", type=int, default=8, help="phash 网格(grid²位;build/live 须一致)")
    ap.add_argument("--sat-th", type=int, default=255,
                    help="抠字 S<此(默认255=【亮度模式】:实测总底池是高饱和青字非白,白模式60抠不到→空hash)")
    ap.add_argument("--val-th", type=int, default=100, help="抠字 V>此(总底池字V120-172、底色V~87 → 100 罩字滤底)")
    args = ap.parse_args()

    prof = json.loads((Path(_ROOT) / "rois" / f"{args.profile}.json").read_text(encoding="utf-8"))
    l, t, w, h = prof["pot_size"]
    res = prof.get("resolution")
    crop_dir = Path(args.crop_out) if args.crop_out else Path(_ROOT) / "tools" / "output" / "pot_label"
    crop_dir.mkdir(parents=True, exist_ok=True)

    label_hashes = []   # [(name, hash)]
    # ① 干净帧来源
    for fp in args.from_frame:
        img = cv2.imread(fp)
        if img is None:
            print(f"  ✗ 读不到 {fp}"); continue
        if res and list(img.shape[1::-1]) != list(res):
            print(f"  ⚠️ {os.path.basename(fp)} 帧尺寸 {img.shape[1::-1]} ≠ profile分辨率 {res} → pot_size 框可能对不上!")
        crop = img[t:t + h, l:l + w]
        name = "frame_" + os.path.splitext(os.path.basename(fp))[0]
        cv2.imwrite(str(crop_dir / f"{name}.png"), crop)   # 眼验裁对没
        hh = text_shape_hash(crop, args.sat_th, args.val_th, grid=args.grid)
        print(f"  {name}: hash len={len(hh)} {'(空=没抠到白字,sat/val要调)' if not hh else ''}")
        if hh:
            label_hashes.append((name, hh))

    # ② label session 来源(正:raw含总+池;负:数字帧)
    num_hashes = []
    if args.session:
        sess = Path(args.session)
        for rec in (json.loads(x) for x in open(sess / "labeled.jsonl", encoding="utf-8") if x.strip()):
            crop = cv2.imread(str(sess / rec["crop"]))
            if crop is None:
                continue
            hh = text_shape_hash(crop, args.sat_th, args.val_th, grid=args.grid)
            if not hh:
                continue
            raw = rec.get("raw_text") or ""
            if not args.from_frame and "总" in raw and "池" in raw:
                label_hashes.append((rec["crop"], hh))
            elif str(rec.get("truth", "")).strip().isdigit():
                num_hashes.append(hh)

    if not label_hashes:
        sys.exit("✗ 没有有效'总底池'正样本(--from-frame 都空 / session 没标注帧)。先看 tools/output/pot_label 裁图,或调阈值。")

    lh = [hh for _, hh in label_hashes]
    intra = [hamming(a, b) for i, a in enumerate(lh) for b in lh[i + 1:]]
    inter = [min(hamming(a, b) for b in lh) for a in num_hashes]
    intra_max = max(intra) if intra else 0
    inter_min = min(inter) if inter else None
    print(f"\n总底池正样本 {len(lh)} 张 {[n for n, _ in label_hashes]}")
    print(f"intra(正样本互相)hamming: {sorted(intra)}  → max={intra_max}")
    if inter_min is not None:
        print(f"inter(数字→最近总底池)hamming: min={inter_min}  (要 > intra_max 才分得开)")
        if inter_min <= intra_max:
            print("  ⚠️ inter ≤ intra:分不开!grid 调大,或把 pot_size 框收窄只罩'总底池'。")
        thr = (intra_max + inter_min) // 2
    else:
        thr = max(intra_max + 4, 12)   # 无负样本:阈给 intra_max 之上的保守值
        print(f"inter: 无数字负样本(没给 session)→ 阈给保守 {thr},live shadow 验漏不漏")
    print(f"→ match_threshold = {thr}")

    out = args.out or str(Path(_ROOT) / "rois" / f"pot_label_phash_{args.profile}.json")
    json.dump({"refs": {"总底池": lh}, "match_threshold": int(thr), "grid": args.grid,
               "sat_th": args.sat_th, "val_th": args.val_th, "margin": 0, "first_char": False},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"✅ 参考已存 {out}(裁图见 {crop_dir} — 打开确认罩住'总底池');live ActionPhashReader.load 读,先 shadow 验")


if __name__ == "__main__":
    main()
