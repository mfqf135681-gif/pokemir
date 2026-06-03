"""pipeline/solver.py — 守恒/合法性求解器(§15 砖2,精度层)

捕获层(reconstruct)给的是【高 recall、带噪声】的候选动作(标准桌 recall 98-99%,
precision ~71%)。本模块用德州下注规则的【结构约束】过滤幻影假阳 —— 不读新数据,
只对已捕获的 ChipAction 序列做合法性裁决。纯 Python、无 torch/cv2、离线可测。

为什么靠 to_amount 结构而非"重读":到达 river 的牌局是【超定】的 —— 每座每街的
累计投入(call-to)必须满足注级单调、不能自己加自己、全下即终。违反者=幻影
(结算期 stack 抖动 / 街错位残影),规则即可拒,无需底池(底池守恒是 v2 增量锚)。

诊断实证(2026-06-03,170343 手1):seat1 下注128(注级=128)后又"跟到200" ——
底池注级只到 128,无人加注到 200,seat1 不能自己把自己加到 200 → 非法幻影。
本层规则 1 即杀此类。

范围:**只删不增**(不补漏抓 —— 漏的是 recall,本层不造数据)。弃/过不在候选内。
设计 doc: requirement-discussions/2026-06-01_95pct-constraint-solver-paradigm.md §20
自测: python pipeline/solver.py
"""

from dataclasses import dataclass, field

PREFLOP, FLOP, TURN, RIVER = "preflop", "flop", "turn", "river"
_STREET_ORDER = {PREFLOP: 0, FLOP: 1, TURN: 2, RIVER: 3}


@dataclass
class SolveResult:
    kept: list = field(default_factory=list)      # list[ChipAction] 通过裁决
    dropped: list = field(default_factory=list)    # list[(ChipAction, reason)]
    notes: list = field(default_factory=list)


def solve_hand(actions, config=None):
    """对【一手】的候选动作做合法性裁决 → (kept, dropped)。
    actions: list[ChipAction](带 seat/street/to_amount/atype/t/confidence)。
    规则(按时间序、分街跑):
      R1 全下终局:某座全下后,该座本手后续动作 = 幻影(全下=投入全部,不能再动)。
      R2 无新增资金:同座同街 to_amount 没真涨(≤该座上次+tol)= 重复读 → 丢。
      R3 不能自加:某座 to_amount 抬到当前注级【之上】,但其间无他人把注级抬过该座旧值
         → 自己加自己,非法(治结算/街错位幻影)。
    返回保留按原序;dropped 记 (action, reason) 供诊断。"""
    config = config or {}
    tol = config.get("tol", 2.0)
    res = SolveResult()
    allin_seats = set()  # 已全下的座(R1,跨街终局)
    # 分街处理注级与各座累计;按 (街序, t) 排序逐个裁决
    ordered = sorted(actions, key=lambda a: (_STREET_ORDER.get(a.street, 0), a.t))
    level_by_street = {}      # street -> 当前注级(最大 to_amount)
    seat_to = {}              # (seat, street) -> 该座本街已确认 to_amount
    for a in ordered:
        st = a.street
        level = level_by_street.get(st, 0.0)
        prev = seat_to.get((a.seat, st), 0.0)
        T = a.to_amount
        # R1:全下后不再行动
        if a.seat in allin_seats:
            res.dropped.append((a, "全下后再行动(幻影)"))
            continue
        # R2:同座同街无新增资金(to_amount 没真涨)→ 重复读
        if T <= prev + tol:
            res.dropped.append((a, "无新增资金(重复读)"))
            continue
        # R3:抬到注级之上 = 加注;须有人把注级抬过该座旧值(否则=自己加自己)
        if T > level + tol:
            if abs(prev - level) <= tol and level > tol:
                # 该座本就在注级上(自己设的/已跟平),无人再加 → 不能自加
                res.dropped.append((a, "自己加自己(无中间加注)"))
                continue
            level = T  # 合法加注/首注 → 抬注级
        # 通过:更新状态
        level_by_street[st] = level
        seat_to[(a.seat, st)] = T
        if a.atype == "all_in":
            allin_seats.add(a.seat)
        res.kept.append(a)
    if res.dropped:
        res.notes.append(f"求解器裁掉 {len(res.dropped)} 笔幻影 / 保留 {len(res.kept)}")
    return res


def _self_test():
    from dataclasses import dataclass as _dc

    @_dc
    class A:  # 轻量 ChipAction 替身(只需 solver 用到的字段)
        seat: int
        street: str
        to_amount: float
        t: float
        atype: str = "?"
        confidence: float = 1.0
        chips_in: float = 0.0

    # R3:170343 手1 真实幻影 — seat1 下注128 → seat3 跟128 → seat1 又"跟到200"(自加,非法)
    acts = [A(1, RIVER, 128, 20.6, "bet"), A(3, RIVER, 128, 23.8, "call"),
            A(1, RIVER, 200, 25.0, "call")]
    r = solve_hand(acts)
    kept = [(a.seat, round(a.to_amount)) for a in r.kept]
    assert kept == [(1, 128), (3, 128)], kept
    assert r.dropped and "自己加自己" in r.dropped[0][1], r.dropped
    print(f"✅ R3 自加幻影:{kept} 留,裁 {[(a.seat, round(a.to_amount), why) for a, why in r.dropped]}")

    # R3 放行合法再加注:seat1 下注100 → seat2 加到300 → seat1 再加到500(回应加注,合法)
    acts2 = [A(1, FLOP, 100, 1, "bet"), A(2, FLOP, 300, 2, "raise"), A(1, FLOP, 500, 3, "raise")]
    r2 = solve_hand(acts2)
    assert len(r2.kept) == 3 and not r2.dropped, (r2.kept, r2.dropped)
    print(f"✅ R3 合法再加注全留:{[(a.seat, round(a.to_amount)) for a in r2.kept]}")

    # R2:同座同街重复读(to_amount 没涨)
    acts3 = [A(5, TURN, 74, 1, "bet"), A(5, TURN, 75, 2, "call")]  # 75-74<=tol → 第2笔重复
    r3 = solve_hand(acts3)
    assert len(r3.kept) == 1 and len(r3.dropped) == 1, (r3.kept, r3.dropped)
    print(f"✅ R2 无新增资金:留 {[(a.seat, round(a.to_amount)) for a in r3.kept]},裁重复读")

    # R1:全下后再行动 → 幻影(seat3 全下 524,后又出 river 动作)
    acts4 = [A(6, RIVER, 23, 301.3, "bet"), A(3, RIVER, 524, 301.3, "all_in"),
             A(3, RIVER, 600, 305.0, "raise")]  # 全下后 seat3 不该再动
    r4 = solve_hand(acts4)
    seats_kept = sorted(a.seat for a in r4.kept)
    assert (3, "all_in") in [(a.seat, a.atype) for a in r4.kept], r4.kept
    assert any(a.seat == 3 and "全下后" in why for a, why in r4.dropped), r4.dropped
    print(f"✅ R1 全下终局:全下留、其后裁 {[(a.seat, round(a.to_amount)) for a, _ in r4.dropped]}")

    # 不误杀正常多街序列:preflop limp+raise、flop bet+call、turn、river 各合法
    acts5 = [A(0, PREFLOP, 10, 1), A(1, PREFLOP, 10, 2), A(2, PREFLOP, 30, 3, "raise"),
             A(0, PREFLOP, 30, 4, "call"), A(1, PREFLOP, 30, 5, "call"),
             A(0, FLOP, 50, 10, "bet"), A(1, FLOP, 50, 11, "call"),
             A(0, RIVER, 200, 20, "bet"), A(1, RIVER, 200, 21, "call")]
    r5 = solve_hand(acts5)
    assert len(r5.kept) == 9 and not r5.dropped, (len(r5.kept), r5.dropped)
    print(f"✅ 正常牌局零误杀:{len(r5.kept)}/9 全留")

    print("\n✅ solver self-test 全过")


if __name__ == "__main__":
    _self_test()
