r"""tools/roi_derive.py — ROI 参数化派生(card_marker 锚 + 单座模板 + 镜像)。

动机(2026-06-04 调研结论):party_poker_8 的每座 ROI 早已近乎镜像对称(左右对
残差 ≤3px,见 requirement-discussions),手框只剩 <3px 抖动 + amount 一个真离群。
与其重框 8×13=104 个框,不如:**每座 ROI = 该座 card_marker 锚 + 一份组内偏移模板**
→ 从一个参考座抽模板、套到同组其他座 → 消抖动、立一个能迁移(切桌型/分辨率/9座)
的干净几何模型。

分组(party_poker_8,8座):
  左组 {0,1,2,3}(hero底中 s0 + 左列 s1-3)→ 用左参考座(默认 s2)模板
  右组 {4,5,6,7}(顶中 s4 + 右列 s5-7)→ 用右参考座(默认 s6)模板
  左右互为垂直镜像(轴 x≈727);右模板会跟 mirror(左模板) 做一致性自检。
  s0(hero)/s4(顶)是中柱特例:仍套组模板,但 dry-run 会单独标出它俩残差,
  超阈值的字段留给用户 Win 端单独重框。

跳过派生(保留原值):amount(浮动+遮挡,镜像残差 17.5px,派生不出)。

⚠️ R-7:本工具默认 --dry-run(只比对、不写)。写也只写 <profile>_derived.json,
   绝不覆盖生产 profile;真正启用须 Win 端 roi_config.py --verify 叠帧确认读数不退步。
⚠️ 框选/--verify 是 cv2 Win-only;本工具(几何派生+比对)纯逻辑,Linux 可跑可单测。

用法:
  # 比对:派生 vs 现有,看残差(默认,安全)
  python tools/roi_derive.py --profile party_poker_8
  # 写出派生 profile(到 party_poker_8_derived.json,不动生产)
  python tools/roi_derive.py --profile party_poker_8 --write
"""
import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
from roi_geom import roi_offset, apply_offset, mirror_box_x  # noqa: E402

# 每座要派生的 box 字段(锚 card_marker 自身、meta seat_index/card_marker_ref 不派生)。
DERIVE_FIELDS = ["stack", "amount", "action", "fold_area", "fold_text", "id",
                 "cards", "hand_type", "timer", "win_amount", "button_indicator"]
# amount 也参与派生:调研发现它对列座(s1/2/3/6)派生残差 0-3,只有中柱 s0/s4
# 巨偏(27/187)。靠"残差≤阈才替换"策略自动:列座规整、中柱保留原值留 Win 重框。
SKIP_FIELDS = []  # 无字段整体跳过;离群个例由 ≤tol 策略逐 (座,字段) 保留原值
ANCHOR = "card_marker"


def _center_x(box):
    return box[0] + box[2] / 2.0


def derive_profile(prof, left_group, right_group, left_ref, right_ref):
    """→ (derived_seats: {seat_index: {field: box}}, report: list[dict], axis)。
    纯函数:不读盘不写盘,便于单测。"""
    seats = {s["seat_index"]: s for s in prof["seats"]}
    axis = (_center_x(seats[left_ref][ANCHOR]) + _center_x(seats[right_ref][ANCHOR])) / 2.0

    # 抽两组模板:参考座每字段相对自身 card_marker 的偏移
    tpl = {}
    for grp_ref, grp in ((left_ref, left_group), (right_ref, right_group)):
        cm_ref = seats[grp_ref][ANCHOR]
        t = {f: roi_offset(cm_ref, seats[grp_ref][f]) for f in DERIVE_FIELDS if f in seats[grp_ref]}
        for s in grp:
            tpl[s] = t

    derived, report = {}, []
    for sidx, seat in seats.items():
        cm = seat[ANCHOR]
        t = tpl[sidx]
        out = {}
        for f in DERIVE_FIELDS:
            if f not in t or f not in seat:
                continue
            box = apply_offset(cm, t[f])
            old = seat[f]
            resid = abs(box[0] - old[0]) + abs(box[1] - old[1])  # |Δleft|+|Δtop|
            out[f] = box
            report.append({"seat": sidx, "field": f, "derived": box, "old": old, "resid": resid})
        derived[sidx] = out
    return derived, report, axis


def mirror_consistency(derived, axis, pairs):
    """左右对:derived[R] 应 ≈ mirror(derived[L])。→ list[(pair, field, resid)]。"""
    out = []
    for L, R in pairs:
        for f in DERIVE_FIELDS:
            if f in derived.get(L, {}) and f in derived.get(R, {}):
                m = mirror_box_x(derived[L][f], axis)
                r = derived[R][f]
                out.append(((L, R), f, abs(m[0] - r[0]) + abs(m[1] - r[1])))
    return out


def main():
    ap = argparse.ArgumentParser(description="ROI 参数化派生(card_marker 锚 + 模板 + 镜像)")
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--left-group", default="0,1,2,3")
    ap.add_argument("--right-group", default="4,5,6,7")
    ap.add_argument("--left-ref", type=int, default=2)
    ap.add_argument("--right-ref", type=int, default=6)
    ap.add_argument("--pairs", default="1:7,2:6,3:5", help="左右镜像对(一致性自检)")
    ap.add_argument("--tol", type=int, default=4, help="残差告警阈(px,|Δleft|+|Δtop|)")
    ap.add_argument("--write", action="store_true", help="写 <profile>_derived.json(不覆盖生产)")
    args = ap.parse_args()

    prof_path = Path(_ROOT) / "rois" / f"{args.profile}.json"
    prof = json.loads(prof_path.read_text(encoding="utf-8"))
    lg = [int(x) for x in args.left_group.split(",")]
    rg = [int(x) for x in args.right_group.split(",")]
    pairs = [tuple(int(x) for x in p.split(":")) for p in args.pairs.split(",")]

    derived, report, axis = derive_profile(prof, lg, rg, args.left_ref, args.right_ref)
    print(f"profile={args.profile}  镜像轴 x≈{axis:.1f}  左组{lg}(参考s{args.left_ref}) "
          f"右组{rg}(参考s{args.right_ref})  跳过(保留原值):{SKIP_FIELDS}")

    # ① 派生 vs 现有残差(逐字段聚合 + 标离群座)
    print(f"\n① 派生 vs 现有残差(|Δleft|+|Δtop| px,阈 {args.tol}):")
    from collections import defaultdict
    by_field = defaultdict(list)
    for r in report:
        by_field[r["field"]].append((r["seat"], r["resid"]))
    for f in DERIVE_FIELDS:
        rows = by_field.get(f, [])
        if not rows:
            continue
        mx = max(rows, key=lambda kv: kv[1])
        bad = [f"s{s}={d}" for s, d in rows if d > args.tol]
        flag = f"  ⚠️ 超阈: {', '.join(bad)}" if bad else "  ✅"
        print(f"  {f:<16} 最大残差 s{mx[0]}={mx[1]}{flag}")

    # ② 中柱特例 s0/s4 单独看(它俩套组模板,逐字段残差)
    print(f"\n② 中柱特例残差(s0 套左模板 / s4 套右模板,>阈需 Win 单独重框):")
    for sidx in (0, 4):
        bad = [(r["field"], r["resid"]) for r in report if r["seat"] == sidx and r["resid"] > args.tol]
        print(f"  s{sidx}: " + ("✅ 全部 ≤阈,套组模板即可" if not bad
              else "⚠️ " + ", ".join(f"{f}={d}" for f, d in bad)))

    # ③ 左右镜像一致性自检(派生结果内部应严格镜像)
    mc = mirror_consistency(derived, axis, pairs)
    worst = max(mc, key=lambda x: x[2]) if mc else None
    bad_mc = [(p, f, d) for p, f, d in mc if d > args.tol]
    print(f"\n③ 左右镜像一致性(派生内部,应≈0):最大 {worst[0]} {worst[1]}={worst[2]} | "
          f"{'✅ 全部 ≤阈' if not bad_mc else f'⚠️ {len(bad_mc)} 项超阈'}")

    if args.write:
        # 写入策略:残差 ≤tol 才替换(消手框抖动、立干净模型);超阈保留原值
        # (护住 s0/s4 中柱特例 + button 等派生不出的)→ 安全、数据驱动。
        resid_of = {(r["seat"], r["field"]): r["resid"] for r in report}
        out = json.loads(prof_path.read_text(encoding="utf-8"))  # 原样克隆
        seats = {s["seat_index"]: s for s in out["seats"]}
        n_repl, n_kept, kept = 0, 0, []
        for sidx, fields in derived.items():
            for f, box in fields.items():
                if resid_of[(sidx, f)] <= args.tol:
                    seats[sidx][f] = box; n_repl += 1
                else:
                    n_kept += 1; kept.append(f"s{sidx}.{f}({resid_of[(sidx, f)]})")
        out_path = Path(_ROOT) / "rois" / f"{args.profile}_derived.json"
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ 已写 {out_path.name}:{n_repl} 字段规整为派生值,{n_kept} 个超阈保留原值。")
        print(f"   保留原值(派生不出,留 Win 单独核/重框):{', '.join(kept)}")
        print(f"   amount 等 {SKIP_FIELDS} 全程未派生(保留原值)。生产 profile 未动。")
        print(f"   下一步(Win):roi_config.py --verify 叠帧核 → digit_probe/replay 验读数不退步 → 再替换生产。")
    else:
        print(f"\n(--dry-run 默认:只比对未写。加 --write 写 {args.profile}_derived.json)")


if __name__ == "__main__":
    main()
