r"""tools/verify_recipe_stack.py — 验【生产配方】读 stack 的准度(对眼标真值)。

加载 live 在用的同一套模板(rois/digit_templates_<profile>.json 经 DigitReader),
对一段录像的 stack ROI 逐帧读,跟用户【肉眼标的真值文件】(帧=v0,..,v7)逐项对,
出准确率 + 列错项。回答杠杆A的"准不准"——用现成录像+现成真值,不用新录 live。

真值文件格式(同 truth_digit_121925.txt):每行 `帧文件=v0,v1,...,v7`(按 seat_index;
空=该座没标/不可读,跳过;非数字=跳过)。# 注释行忽略。

⚠️ cv2 Win-only;纯解析/比对在 Linux 可测。
用法(Win):
  python tools\verify_recipe_stack.py --session data\recordings\20260603_121925 ^
      --truth tools\truth_digit_121925.txt
  # 显式模板:--templates rois\digit_templates_party_poker_8.json(默认按 --profile 找)
"""
import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "pipeline"))


def _parse_truth(path):
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        fn, vals = line.split("=", 1)
        out.append((fn.strip(), [v.strip() for v in vals.split(",")]))
    return out


def main():
    ap = argparse.ArgumentParser(description="验生产配方读 stack 准度(对眼标真值)")
    ap.add_argument("--session", required=True, help=r"录像目录 data\recordings\<ts>")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--truth", required=True, help="眼标真值文件(帧=v0,..,v7)")
    ap.add_argument("--templates", default="", help="模板 JSON;空=rois/digit_templates_<profile>.json")
    ap.add_argument("--field", default="stack")
    args = ap.parse_args()

    import cv2
    import digit_reader

    tmpl = args.templates or str(Path(_ROOT) / "rois" / f"digit_templates_{args.profile}.json")
    if not Path(tmpl).is_file():
        print(f"❌ 无模板 {tmpl}(先跑 build_digit_templates.py)"); sys.exit(1)
    reader = digit_reader.DigitReader.load(tmpl)
    print(f"📐 生产配方模板 {Path(tmpl).name}(字符 {sorted(reader.exemplars)})")

    prof = json.loads((Path(_ROOT) / "rois" / f"{args.profile}.json").read_text(encoding="utf-8"))
    seat_rois = {s["seat_index"]: s[args.field] for s in prof["seats"] if s.get(args.field)}
    fdir = Path(args.session) / "frames"

    blocks = _parse_truth(args.truth)
    ok = bad = miss = 0
    errs = []
    for fn, vals in blocks:
        img = cv2.imread(str(fdir / fn))
        if img is None:
            print(f"  ⚠️ 读不到帧 {fn}"); continue
        for si, truth in enumerate(vals):
            if not truth or not truth.isdigit() or si not in seat_rois:
                continue
            l, t, w, h = seat_rois[si]
            got = reader.read(img[t:t + h, l:l + w])
            if got is None:
                miss += 1
                errs.append((fn, si, truth, "None(读空/回落)"))
            elif str(got) == truth:
                ok += 1
            else:
                bad += 1
                errs.append((fn, si, truth, str(got)))

    tot = ok + bad + miss
    print(f"\n=== 生产配方 stack 准度(对眼标真值)===")
    print(f"  样本 {tot}(座×帧)  对 {ok}  错 {bad}  读空 {miss}")
    if tot:
        print(f"  ★ 准确率 {ok}/{tot} = {100.0*ok/tot:.1f}%   (含读空算未命中;纯读对率 {ok}/{ok+bad}={100.0*ok/(ok+bad) if ok+bad else 0:.1f}%)")
    for fn, si, truth, got in errs[:40]:
        print(f"  ✗ {fn} s{si} 真值 {truth} → {got}")
    if not errs:
        print("  ✅ 全对,无错项。")


if __name__ == "__main__":
    main()
