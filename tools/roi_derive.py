r"""tools/roi_derive.py — ROI 参数化派生(card_marker 锚 + 单模板 + 镜像,全座统一)。

动机(2026-06-04 调研):party_poker_8 左右座近乎镜像(轴 x≈727),手框只剩抖动 +
中柱座 s0/s4 特例。**镜像派生**:右座 = 左座镜像(镜像保 w/h → 左右必然同尺寸 +
位置严格对称),从【一个左参考座】抽模板 → 全列座统一。

模型(单一真相源 = 左模板 T_L,从 left-ref 抽):
  左列 {1,2,3} + hero s0  : box = 锚 + T_L            (左型)
  右列 {5,6,7} + 顶 s4    : box = 锚 + mirror(T_L)     (右型;mirror 保 w/h)
  右模板 T_R 由 T_L 解析镜像:dx_R = cm_w − w − dx_L, dy/w/h 不变(无需轴,相对自身锚)。
  → 列座 6 个全统一尺寸 + 镜像对称;**中柱 s0/s4** 走"残差>tol 保留原值"(留 Win 重框)。

策略:
  列座(left_col + right_col)→ **强制**用派生值(求统一,不 gate)。
  中柱(center {0,4})→ 残差 ≤tol 才替换、超阈保留原值(护 amount/button 等真离群)。

⚠️ R-7:默认 --dry-run;--write 只写 <profile>_derived.json,不覆盖生产。
   启用须 Win roi_config --verify --frame 叠帧核 + replay 验读数不退步 → 再替换。
⚠️ 框选/--verify cv2 Win-only;本工具(几何派生+比对)纯逻辑 Linux 可跑可单测。

用法:
  python tools/roi_derive.py --profile party_poker_8            # dry-run 比对
  python tools/roi_derive.py --profile party_poker_8 --write    # 写 _derived.json
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
from roi_geom import roi_offset, apply_offset, mirror_box_x  # noqa: E402

DERIVE_FIELDS = ["stack", "amount", "action", "fold_area", "fold_text", "id",
                 "cards", "hand_type", "anchor", "win_amount", "button_indicator"]
ANCHOR = "card_marker"


def _center_x(box):
    return box[0] + box[2] / 2.0


def _mirror_template(t_l, cm_w):
    """左模板 → 右模板(相对自身锚的镜像):w/h 不变,dx 翻为 cm_w − w − dx_L,dy 不变。
    推导:右 box = mirror(左等价 box);因表达成"相对右座自身 card_marker 偏移",轴自动消去。"""
    dx, dy, w, h = t_l
    return (cm_w - w - dx, dy, w, h)


def derive_profile(prof, left_col, right_col, center, left_ref):
    """→ (derived {seat:{field:box}}, report list[dict], axis)。纯函数,可单测。
    左型(left_col + s0)套 T_L;右型(right_col + s4)套 mirror(T_L)。"""
    seats = {s["seat_index"]: s for s in prof["seats"]}
    right_type = set(right_col) | {c for c in center if c == 4}
    left_type = set(left_col) | {c for c in center if c == 0}
    t_l = {f: roi_offset(seats[left_ref][ANCHOR], seats[left_ref][f])
           for f in DERIVE_FIELDS if f in seats[left_ref]}

    # 镜像轴(仅供一致性自检):左右参考座 card_marker 中心中点
    rr = next(iter(right_col))
    axis = (_center_x(seats[left_ref][ANCHOR]) + _center_x(seats[rr][ANCHOR])) / 2.0

    derived, report = {}, []
    for sidx, seat in seats.items():
        anchor = seat[ANCHOR]
        cm_w = anchor[2]
        out = {}
        for f in DERIVE_FIELDS:
            if f not in t_l or f not in seat:
                continue
            tpl = _mirror_template(t_l[f], cm_w) if sidx in right_type else t_l[f]
            box = apply_offset(anchor, tpl)
            old = seat[f]
            out[f] = box
            report.append({"seat": sidx, "field": f, "derived": box, "old": old,
                           "resid": abs(box[0] - old[0]) + abs(box[1] - old[1]),
                           "is_center": sidx in center})
        derived[sidx] = out
    return derived, report, axis


def mirror_consistency(derived, axis, pairs):
    out = []
    for L, R in pairs:
        for f in DERIVE_FIELDS:
            if f in derived.get(L, {}) and f in derived.get(R, {}):
                m = mirror_box_x(derived[L][f], axis)
                r = derived[R][f]
                out.append(((L, R), f, abs(m[0] - r[0]) + abs(m[1] - r[1])))
    return out


def main():
    ap = argparse.ArgumentParser(description="ROI 镜像派生(单左模板 → 全座统一)")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--left-col", default="1,2,3", help="左列座(强制套左模板)")
    ap.add_argument("--right-col", default="5,6,7", help="右列座(强制套镜像模板)")
    ap.add_argument("--center", default="0,4", help="中柱特例座(残差>tol 保留原值,留 Win 重框)")
    ap.add_argument("--left-ref", type=int, default=2, help="左模板参考座")
    ap.add_argument("--pairs", default="1:7,2:6,3:5", help="左右镜像对(一致性自检)")
    ap.add_argument("--tol", type=int, default=4, help="中柱残差告警/保留阈(px)")
    ap.add_argument("--write", action="store_true", help="写 <profile>_derived.json(不覆盖生产)")
    args = ap.parse_args()

    prof_path = Path(_ROOT) / "rois" / f"{args.profile}.json"
    prof = json.loads(prof_path.read_text(encoding="utf-8"))
    lc = [int(x) for x in args.left_col.split(",")]
    rc = [int(x) for x in args.right_col.split(",")]
    ce = [int(x) for x in args.center.split(",")]
    pairs = [tuple(int(x) for x in p.split(":")) for p in args.pairs.split(",")]

    derived, report, axis = derive_profile(prof, lc, rc, ce, args.left_ref)
    print(f"profile={args.profile}  轴 x≈{axis:.1f}  左列{lc}+hero / 右列{rc}+顶  "
          f"左模板参考 s{args.left_ref}  中柱{ce}(残差>{args.tol} 保留原值)")

    # ① 列座:残差(应小,强制统一)+ 尺寸是否全一致
    print("\n① 列座派生(强制统一):各字段尺寸 + 最大位置残差")
    byf = defaultdict(list)
    for r in report:
        if not r["is_center"]:
            byf[r["field"]].append(r)
    for f in DERIVE_FIELDS:
        rs = byf.get(f, [])
        if not rs:
            continue
        sizes = {(r["derived"][2], r["derived"][3]) for r in rs}
        mx = max(rs, key=lambda r: r["resid"])
        uni = "✅尺寸统一" if len(sizes) == 1 else f"⚠️尺寸{len(sizes)}种"
        print(f"  {f:<16} {list(sizes)[0][0]}×{list(sizes)[0][1]}  最大位置残差 s{mx['seat']}={mx['resid']}  {uni}")

    # ② 中柱 s0/s4:残差>tol 的字段(写时保留原值,留 Win 重框)
    print(f"\n② 中柱特例(残差>{args.tol} 保留原值,Win 重框):")
    for c in ce:
        bad = [(r["field"], r["resid"]) for r in report if r["seat"] == c and r["resid"] > args.tol]
        print(f"  s{c}: " + ("✅ 全部≤阈,套模板即可" if not bad
              else ", ".join(f"{f}={d}" for f, d in bad)))

    # ③ 镜像一致性
    mc = mirror_consistency(derived, axis, pairs)
    worst = max(mc, key=lambda x: x[2]) if mc else None
    print(f"\n③ 左右镜像一致性(派生内部应≈0):最大 {worst[0]} {worst[1]}={worst[2]} "
          f"| {'✅' if all(d <= args.tol for _, _, d in mc) else '⚠️有超阈'}")

    if args.write:
        out = json.loads(prof_path.read_text(encoding="utf-8"))
        seats = {s["seat_index"]: s for s in out["seats"]}
        n_repl, kept = 0, []
        for r in report:
            sidx, f = r["seat"], r["field"]
            if r["is_center"] and r["resid"] > args.tol:
                kept.append(f"s{sidx}.{f}({r['resid']})")          # 中柱离群:保留原值
            else:
                seats[sidx][f] = r["derived"]; n_repl += 1          # 列座 + 中柱合身:派生
        out_path = Path(_ROOT) / "rois" / f"{args.profile}_derived.json"
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ 已写 {out_path.name}:{n_repl} 字段派生(列座全统一+镜像),{len(kept)} 保留原值:")
        print(f"   {', '.join(kept) if kept else '(无)'}")
        print(f"   card_marker(锚)/结构未动;生产 profile 未动。")
        print(f"   下一步(Win):roi_config --verify --frame 逐元素核 → 重框中柱 → replay 验读数 → 替换。")
    else:
        print(f"\n(--dry-run 默认。加 --write 写 {args.profile}_derived.json)")


if __name__ == "__main__":
    main()
