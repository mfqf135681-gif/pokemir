"""pipeline/reconstruct.py — 重建逻辑(§15 架构「大脑」,砖 1a)

把【每座 stack 时间序列 + 公共牌序列 + 桌规】重建为【每玩家每街的筹码动作】。
纯 Python、无 torch/cv2、离线可测。这是 §15 stack-主探测架构的核心逻辑,
与「读 stack 的识别层」(砖 2,Win/GPU)解耦 —— 先证逻辑对,再优化速度。

链条(§15):
  stack 时间序列 → 稳态融合(filter 噪声/None)→ 检测下跌 = 本次投入(chips_in)
  → 按公共牌进度分街 → 规则推动作类型(call/bet/raise/all_in)
  → 全局守恒校验(Σ chips_in + 强制注 ≈ pot)
  → (可选)局部恒等式:chips_in ≈ 下注区读数(三腿互验)

范围:**筹码动作**(call/bet/raise/all_in)。弃/过(零跌幅)靠活跃集另判,不在此。
强制注(ante/盲)由桌规已知 → 单列,不混入自愿动作。

设计 doc: requirement-discussions/2026-06-01_95pct-constraint-solver-paradigm.md §15
自测: python pipeline/reconstruct.py  (用 §14 Ts2h5h 真值构造轨迹验证)
"""

from dataclasses import dataclass, field

PREFLOP, FLOP, TURN, RIVER = "preflop", "flop", "turn", "river"


@dataclass
class ChipAction:
    seat: int
    street: str
    chips_in: float          # 本次投入(= stack 跌幅)
    t: float
    atype: str = "?"         # 规则推:call/bet/raise/all_in
    confidence: float = 1.0  # 局部恒等式对上=1.0,缺校验<1


@dataclass
class ReconResult:
    actions: list = field(default_factory=list)        # list[ChipAction]
    forced: dict = field(default_factory=dict)          # seat -> 强制注(ante+盲)
    sum_chips: float = 0.0                               # Σ自愿+强制
    pot_observed: float = 0.0
    conserved: bool = False
    notes: list = field(default_factory=list)


def fuse_plateaus(series, tol=2.0, min_run=2):
    """noisy [(t, val|None)] → 稳态平台 [(t_start, value)].
    min_run 过滤单帧离群(§15 稳态融合);tol 容忍末位读取抖动。"""
    runs = []  # [t_start, value, count]
    for t, v in series:
        if v is None:
            continue
        if runs and abs(v - runs[-1][1]) <= tol:
            runs[-1][2] += 1
        else:
            runs.append([t, float(v), 1])
    return [(r[0], r[1]) for r in runs if r[2] >= min_run]


def detect_drops(plateaus):
    """稳态平台 → 下跌事件 [(t, chips_in)];涨(派彩/补码)忽略并记。"""
    drops, rises = [], []
    for i in range(1, len(plateaus)):
        d = plateaus[i - 1][1] - plateaus[i][1]
        if d > 0:
            drops.append((plateaus[i][0], d))
        elif d < 0:
            rises.append((plateaus[i][0], -d))
    return drops, rises


def street_at(t, boundaries):
    """boundaries: list[(street, t_start)] 升序 → t 落在哪条街。"""
    s = PREFLOP
    for street, t0 in boundaries:
        if t >= t0:
            s = street
    return s


def boundaries_from_community(community_series):
    """[(t, n_cards)] → [(street, t_start)]。0→preflop 3→flop 4→turn 5→river。"""
    seen = {}
    for t, n in community_series:
        st = {0: PREFLOP, 3: FLOP, 4: TURN, 5: RIVER}.get(n)
        if st and st not in seen:
            seen[st] = t
    out = [(PREFLOP, community_series[0][0] if community_series else 0.0)]
    for st in (FLOP, TURN, RIVER):
        if st in seen:
            out.append((st, seen[st]))
    return out


def reconstruct(stack_series, community_series, config, bet_reads=None):
    """
    stack_series: {seat: [(t, stack_val|None), ...]}  每座 stack 时间序列
    community_series: [(t, n_community_cards), ...]
    config: {"sb":2,"bb":4,"ante":4,"pot":470,"sb_seat":i,"bb_seat":j,"seats":[...]}
    bet_reads: optional {seat: [(t, bet_area_val), ...]} 下注区读数(局部恒等式校验)
    """
    res = ReconResult(pot_observed=float(config.get("pot", 0)))
    boundaries = boundaries_from_community(community_series)
    ante = config.get("ante", 0)
    seats = config.get("seats", list(stack_series.keys()))

    # 强制注(桌规已知,不靠读):每座 ante + SB/BB
    for s in seats:
        f = ante
        if s == config.get("sb_seat"):
            f += config.get("sb", 0)
        if s == config.get("bb_seat"):
            f += config.get("bb", 0)
        res.forced[s] = f

    # 自愿动作:每座 stack 跌幅(扣掉该座的强制注 baseline 已在轨迹外处理 —
    #   约定:stack_series 的首个平台 = 强制注已扣后的"行动前"持有筹码)
    cur_bet = {}  # street -> 当前最大单家投入(判 call/raise)
    for s in seats:
        plats = fuse_plateaus(stack_series.get(s, []), tol=config.get("tol", 2.0))
        drops, rises = detect_drops(plats)
        for (t, chips) in drops:
            st = street_at(t, boundaries)
            allin = plats and plats[-1][1] <= config.get("tol", 2.0)  # 末平台≈0 → all-in
            # 规则推类型
            mx = cur_bet.get(st, 0)
            if allin and (t == plats[-1][0]):
                atype = "all_in"
            elif chips <= mx + config.get("tol", 2.0):
                atype = "call"
            elif mx == 0:
                atype = "bet"
            else:
                atype = "raise"
            cur_bet[st] = max(mx, chips)
            act = ChipAction(seat=s, street=st, chips_in=chips, t=t, atype=atype)
            # 局部恒等式校验(三腿):chips_in ≈ 下注区读数
            if bet_reads and s in bet_reads:
                near = [bv for (bt, bv) in bet_reads[s] if abs(bt - t) <= config.get("t_tol", 1.0)]
                if near:
                    if min(abs(bv - chips) for bv in near) <= config.get("tol", 2.0):
                        act.confidence = 1.0
                    else:
                        act.confidence = 0.5
                        res.notes.append(f"seat{s}@{t}: stack跌{chips}≠下注区{near} → 低置信")
            res.actions.append(act)
        for (t, amt) in rises:
            res.notes.append(f"seat{s}@{t}: stack 涨 {amt}(派彩/补码,非动作)")

    res.sum_chips = sum(a.chips_in for a in res.actions) + sum(res.forced.values())
    gap = abs(res.sum_chips - res.pot_observed)
    res.conserved = gap <= max(config.get("tol", 2.0), 0.05 * res.pot_observed + 5)
    res.notes.append(f"守恒:Σ投入(自愿{sum(a.chips_in for a in res.actions):.0f}+强制{sum(res.forced.values()):.0f})"
                     f"={res.sum_chips:.0f} vs pot {res.pot_observed:.0f} → {'OK' if res.conserved else 'GAP %.0f' % gap}")
    return res


# ── 自测:用 §14 Ts2h5h 真值构造 stack 轨迹,验证重建 ──────────────────────
def _self_test():
    # Ts2h5h:8 座,ante4,SB2,BB4。摊牌2人 all-in213+call213;其余弃(部分limp4)
    # 构造每座 stack 时间序列(行动前=强制注已扣;含 None/单帧离群噪声测稳态融合)
    # seat0=all-in 赢家:行前持有213 → all-in →0;seat1=caller:行前500 →跟213→287
    F = lambda v: [(0, v), (1, v), (2, None), (3, v)]  # 稳态(带 None + 隐含离群容忍)
    stack_series = {
        0: [(0, 213), (1, 213), (2, 999), (3, 213), (4, 213),  # 999=单帧离群,应被 min_run 滤
            (5, 0), (6, 0), (7, 0)],                            # all-in →0(flop 期)
        1: [(0, 500), (1, 500), (2, 500),
            (5, 287), (6, 287), (7, 287)],                      # 跟注 213(500→287)
        2: [(0, 100), (1, 100), (2, 100), (3, 100)],            # 限注者(已扣),弃,不再变
    }
    community = [(0, 0), (0.5, 0), (4.5, 3)]  # preflop → flop@4.5(all-in/call 在 flop)
    config = {"sb": 2, "bb": 4, "ante": 4, "pot": 470, "seats": [0, 1, 2, 3, 4, 5, 6, 7],
              "sb_seat": 6, "bb_seat": 7}
    # 其余 5 座只 ante(强制),无自愿动作(不放 series = 无 drop)
    res = reconstruct(stack_series, community, config)

    # 断言:seat0 的 all-in=213,seat1 的 call=213,各 1 个动作
    a0 = [a for a in res.actions if a.seat == 0]
    a1 = [a for a in res.actions if a.seat == 1]
    assert len(a0) == 1 and abs(a0[0].chips_in - 213) < 1, a0
    assert a0[0].atype == "all_in", a0[0].atype
    assert len(a1) == 1 and abs(a1[0].chips_in - 213) < 1, a1
    # 离群 999 被滤(没产生假动作):seat0 只 1 个动作
    assert len([a for a in res.actions if a.seat == 0]) == 1
    print("重建动作:")
    for a in sorted(res.actions, key=lambda x: x.t):
        print(f"  seat{a.seat} {a.street} {a.atype} 投入{a.chips_in:.0f} @t{a.t} conf{a.confidence}")
    print("\n".join("  " + n for n in res.notes))
    # 守恒:自愿 213+213=426 + 强制(8×4=32 + SB2 + BB4=38)= 464 ≈ pot 470(差6,街内limp未建模,容忍内)
    print(f"\n守恒 conserved={res.conserved}")
    print("\n✅ self-test 通过:从 stack 轨迹重建出 all-in 213 + call 213,离群被滤,守恒近似")

    # 局部恒等式校验测试:给个对不上的下注区读数 → 应标低置信
    res2 = reconstruct({0: stack_series[0]}, community, {**config, "seats": [0]},
                       bet_reads={0: [(5, 999)]})  # 下注区谎报 999 vs stack 跌 213
    bad = [a for a in res2.actions if a.confidence < 1.0]
    assert bad, "局部恒等式应抓出 stack≠下注区"
    print(f"✅ 局部恒等式抓出不一致(conf={bad[0].confidence}):{res2.notes[0]}")


if __name__ == "__main__":
    _self_test()
