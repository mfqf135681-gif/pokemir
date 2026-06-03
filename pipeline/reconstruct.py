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
    """[(t, n_cards)] → [(street, t_start)]。单调阈值(首次 n≥3=flop / ≥4=turn / ≥5=river),
    忽略瞬时回落抖动(某帧板位误读低不影响已过的街)。
    关键:切片开头常带上一手残留的板(段边界 cut 在派彩,早于 board 归零),先跳到
    首次清板(n≤2 = 真实发牌点)再起算街,否则残留板把 preflop 窗压没、limp 全判 flop。"""
    if not community_series:
        return [(PREFLOP, 0.0)]
    reset_i = next((i for i, (_, n) in enumerate(community_series) if n <= 2), 0)
    series = community_series[reset_i:]
    out = [(PREFLOP, series[0][0])]
    for st, thr in ((FLOP, 3), (TURN, 4), (RIVER, 5)):
        t0 = next((t for (t, n) in series if n >= thr), None)
        if t0 is not None:
            out.append((st, t0))
    return out


def reconstruct(stack_series, community_series, config, bet_reads=None, allin_marks=None):
    """
    stack_series: {seat: [(t, stack_val|None), ...]}  每座 stack 时间序列
    community_series: [(t, n_community_cards), ...]
    config: {"sb":2,"bb":4,"ante":4,"pot":470,"sb_seat":i,"bb_seat":j,"seats":[...]}
    bet_reads: optional {seat: [(t, bet_area_val), ...]} 下注区读数(局部恒等式校验)
    allin_marks: optional {seat: [t, ...]} 读取层标记的 all-in 时刻(stack 区显胜率%)
                 → 该座 all-in,金额=标记前最后已知 stack(治 R15:%盖筹码致 all-in 漏抓)
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
    tol = config.get("tol", 2.0)
    # 先收集全部投入事件(跨座),按时间序排 → 才能正确判 call/bet/raise(当前注随时间推进)
    raw = []  # (t, seat, chips, is_allin)
    for s in seats:
        plats = fuse_plateaus(stack_series.get(s, []), tol=tol)
        drops, rises = detect_drops(plats)
        last_t = plats[-1][0] if plats else None
        last_v = plats[-1][1] if plats else None
        for (t, chips) in drops:
            is_allin = (last_v is not None and last_v <= tol and t == last_t)
            raw.append((t, s, chips, is_allin))
        for (t, amt) in rises:
            res.notes.append(f"seat{s}@{t}: stack 涨 {amt}(派彩/补码,非动作)")

    # 🔧 BUG1 修:ante 排除 —— 手起始全员各跌≈ante 的【最早同时簇】= 强制 ante(已在 res.forced),
    #    不计自愿动作(否则每人 -ante 被误判 limp,虚增 + 吞掉真 limp 可分辨性)。
    ante_marks = set()  # {(seat, round(t,1))}
    if ante > 0:
        ad = sorted((t, s) for (t, s, c, _) in raw if abs(c - ante) <= tol)
        if ad:
            win = config.get("ante_cluster_win", 2.0)
            min_n = config.get("min_ante_seats", 3)
            t0 = ad[0][0]
            cluster = [(t, s) for (t, s) in ad if t - t0 <= win]
            if len(cluster) >= min_n:  # 够多座同时 -ante = ante 簇(非零散 limp)
                ante_marks = {(s, round(t, 1)) for (t, s) in cluster}
                res.notes.append(f"ante 簇排除 {len(ante_marks)} 笔(@t≈{t0:.1f},各≈{ante})→ 计强制注非自愿")
    events = [(t, s, c, a) for (t, s, c, a) in raw if (s, round(t, 1)) not in ante_marks]
    events.sort(key=lambda e: e[0])  # 时间序
    cur_bet = {}  # street -> 当前最大单家投入
    for (t, s, chips, is_allin) in events:
        st = street_at(t, boundaries)
        mx = cur_bet.get(st, 0)
        if is_allin:
            atype = "all_in"
        elif mx == 0:
            atype = "bet"            # 本街第一个投入 = 下注
        elif chips <= mx + tol:
            atype = "call"
        else:
            atype = "raise"
        cur_bet[st] = max(mx, chips)
        act = ChipAction(seat=s, street=st, chips_in=chips, t=t, atype=atype)
        if bet_reads and s in bet_reads:  # 局部恒等式三腿校验
            near = [bv for (bt, bv) in bet_reads[s] if abs(bt - t) <= config.get("t_tol", 1.0)]
            if near:
                act.confidence = 1.0 if min(abs(bv - chips) for bv in near) <= tol else 0.5
                if act.confidence < 1.0:
                    res.notes.append(f"seat{s}@{t}: stack跌{chips}≠下注区{near} → 低置信")
        res.actions.append(act)

    # 🔧 BUG2 修:all-in 识别 —— 读取层标记某座 stack 区显胜率%(筹码被盖)→ all-in。
    #    金额 = 标记前最后已知 stack(他把剩余全推);若该座该街已有动作捕获则跳过(防重复)。
    if allin_marks:
        t_tol = config.get("t_tol", 1.0)
        for s, ts in allin_marks.items():
            plats = fuse_plateaus(stack_series.get(s, []), tol=tol)
            for tm in ts:
                prior = [(t, v) for (t, v) in plats if t <= tm + t_tol]
                if not prior or prior[-1][1] <= tol:
                    continue  # 无前置 stack 或已≈0(归零型 all-in 已被 is_allin 抓)
                if any(a.seat == s and abs(a.t - tm) <= t_tol for a in res.actions):
                    continue  # 该座该时刻已有动作
                st = street_at(tm, boundaries)
                res.actions.append(ChipAction(seat=s, street=st, chips_in=prior[-1][1],
                                              t=tm, atype="all_in", confidence=0.7))
                res.notes.append(f"seat{s}@{tm:.1f}: 胜率%标记 → all-in 反解投入≈{prior[-1][1]:.0f}")

    res.sum_chips = sum(a.chips_in for a in res.actions) + sum(res.forced.values())
    gap = abs(res.sum_chips - res.pot_observed)
    res.conserved = gap <= max(config.get("tol", 2.0), 0.05 * res.pot_observed + 5)
    res.notes.append(f"守恒:Σ投入(自愿{sum(a.chips_in for a in res.actions):.0f}+强制{sum(res.forced.values()):.0f})"
                     f"={res.sum_chips:.0f} vs pot {res.pot_observed:.0f} → {'OK' if res.conserved else 'GAP %.0f' % gap}")
    return res


# ── §17/T129 融合:下注区 amount 当独立第二信号 ──────────────────────────
def actions_from_bets(bet_reads, community_series, config):
    """下注区 amount(每座本街【累计】投入)→ 用街内增量反推动作,**独立于 stack**。
    amount 在街内单调升,增量=本次 chips_in;街切换基线归零;某值需出现≥2 次(滤单帧误读)。
    返回 [(t, seat, street, chips_in)]。"""
    from collections import Counter
    boundaries = boundaries_from_community(community_series)
    tol = config.get("tol", 2.0)
    out = []
    for s, series in bet_reads.items():
        by_street = {}
        for (t, v) in sorted(series):
            if v is None:
                continue
            st = street_at(t, boundaries)
            by_street.setdefault(st, []).append((t, float(v)))
        for st, vals in by_street.items():
            counts = Counter(round(v) for _, v in vals)
            seen = 0.0
            for (t, v) in vals:
                if v > seen + tol and counts[round(v)] >= 2:  # 街内累计新高 + 持久≥2
                    out.append((t, s, st, v - seen))
                    seen = v
    out.sort(key=lambda e: e[0])
    return out


def bet_callto_candidates(bet_reads, community_series, config):
    """下注区 amount 的【累计 call-to 值】(非增量),作真值比对候选 —— 匹配用户"跟到X/加到X"口径。
    每座每街:持久≥2 帧的稳定值。返回 [(street, callto)]。治 §19 call-to vs 增量假象 + 补 all-in
    (all-in 推的钱在注区显示,即使 stack 显%)。"""
    from collections import Counter
    boundaries = boundaries_from_community(community_series)
    tol = config.get("tol", 2.0)
    out = []
    for s, series in bet_reads.items():
        by_street = {}
        for (t, v) in sorted(series):
            if v is None:
                continue
            by_street.setdefault(street_at(t, boundaries), []).append(round(float(v)))
        for st, vals in by_street.items():
            for val, c in Counter(vals).items():
                if c >= 2 and val > tol:  # 持久(滤单帧误读)且非零
                    out.append((st, float(val)))
    return out


def compare_coverage(stack_actions, bet_actions, tol=2.0, t_tol=3.0):
    """stack 派生动作 vs 下注区派生动作,按 (seat, street, ~t) 匹配 → 覆盖+一致性统计。
    无真值时的天花板代理:union=两信号并集;agree=都抓到且金额合;*_only=互补恢复量。"""
    sa = [(a.seat, a.street, a.t, a.chips_in) for a in stack_actions]
    ba = list(bet_actions)  # (t, seat, street, chips)
    used = [False] * len(ba)
    agree = dis = stack_only = 0
    for (s, st, t, c) in sa:
        hit, best_dt = -1, 1e9  # 同街多动作:按【最近时间】配,不是窗口内第一个
        for j, (bt, bs, bst, bc) in enumerate(ba):
            if used[j] or bs != s or bst != st:
                continue
            dt = abs(bt - t)
            if dt <= t_tol and dt < best_dt:
                hit, best_dt = j, dt
        if hit >= 0:
            used[hit] = True
            if abs(ba[hit][3] - c) <= tol:
                agree += 1
            else:
                dis += 1
        else:
            stack_only += 1
    bet_only = sum(1 for j in range(len(ba)) if not used[j])
    return {"both_agree": agree, "both_disagree": dis, "stack_only": stack_only,
            "bet_only": bet_only, "n_stack": len(sa), "n_bet": len(ba),
            "union": agree + dis + stack_only + bet_only}


# ── 手分段(§15.3:录制跨多手 → 按边界切成一手一窗)──────────────────────
def segment_hands(stack_series, community_series, config):
    """检测手边界 → [(t_start, t_end)] 每手窗口。
    边界信号(真实数据 §15.3):派彩大涨 / 全员 ante 小跌簇 / 公共牌 reset。"""
    tol = config.get("tol", 2.0)
    payout_th = config.get("payout_th", max(config.get("bb", 4) * 10, 50))
    ante = config.get("ante", 0)
    bounds = set()
    # 1) 派彩:任一座平台大涨
    for obs in stack_series.values():
        plats = fuse_plateaus(obs, tol=tol)
        for i in range(1, len(plats)):
            if plats[i][1] - plats[i - 1][1] > payout_th:
                bounds.add(round(plats[i][0], 1))
    # 2) ante 簇:相近时间多座各跌 ≈ante
    if ante > 0:
        ad = []
        for obs in stack_series.values():
            plats = fuse_plateaus(obs, tol=tol)
            for i in range(1, len(plats)):
                if abs((plats[i - 1][1] - plats[i][1]) - ante) <= tol:
                    ad.append(plats[i][0])
        ad.sort()
        win = config.get("ante_cluster_win", 2.0)
        min_n = config.get("min_ante_seats", 3)
        i = 0
        while i < len(ad):
            j = i
            while j < len(ad) and ad[j] - ad[i] <= win:
                j += 1
            if j - i >= min_n:
                bounds.add(round(ad[i], 1))
            i = j
    # 3) 公共牌 reset(n 下降)
    for k in range(1, len(community_series)):
        if community_series[k][1] < community_series[k - 1][1]:
            bounds.add(round(community_series[k][0], 1))
    # 合并相近边界
    merged = []
    bm = config.get("boundary_merge", 6.0)  # 几秒内的边界信号(公共牌reset/派彩/ante)当一个
    for b in sorted(bounds):
        if merged and b - merged[-1] <= bm:
            continue
        merged.append(b)
    # 构造窗口
    allt = [t for obs in stack_series.values() for (t, _) in obs] + [t for (t, _) in community_series]
    if not allt:
        return []
    t0, tN = min(allt), max(allt)
    cuts = sorted(set([t0] + merged + [tN + 0.01]))
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1) if cuts[i + 1] - cuts[i] > 0.5]


def slice_series(stack_series, community_series, t0, t1):
    ss = {s: [(t, v) for (t, v) in obs if t0 <= t < t1] for s, obs in stack_series.items()}
    cs = [(t, n) for (t, n) in community_series if t0 <= t < t1]
    return ss, cs


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

    # 手分段自测:2 手(A: seat0 下注后派彩涨到 800;B: t10.2 全员 ante -4)
    ss2 = {
        0: [(0, 300), (1, 300), (5, 150), (6, 150), (10, 800), (11, 800)],  # A 下注150 → 派彩 @10
        1: [(0, 200), (1, 200), (5, 50), (6, 50)],                          # A 下注150
        2: [(0, 100), (1, 100), (10.2, 96), (11, 96)],                      # B ante -4
        3: [(0, 120), (1, 120), (10.2, 116), (11, 116)],                    # B ante -4
        4: [(0, 90), (1, 90), (10.2, 86), (11, 86)],                        # B ante -4
    }
    wins = segment_hands(ss2, [], {"bb": 4, "ante": 4, "tol": 2.0})
    assert len(wins) == 2, wins
    assert abs(wins[0][1] - 10) <= 1.5, wins  # 边界 ≈10
    print(f"✅ 手分段:2 手窗口 {[(round(a, 1), round(b, 1)) for a, b in wins]}(派彩涨+全员ante 边界 ≈10)")

    # §17/T129 融合自测:下注区 amount 累计序列 → 反推动作 + 覆盖对比
    comm = [(0, 0), (5, 3)]  # preflop→flop@5
    bet_reads = {
        # seat1:preflop 累计 4(limp)→58(re-raise 到 58);flop 累计 28。每值出现≥2 次。
        1: [(0, None), (1, 4), (2, 4), (3, 58), (4, 58), (6, 28), (7, 28), (8, None)],
    }
    bacts = actions_from_bets(bet_reads, comm, {"tol": 2.0})
    # 期望:preflop 4(增量4)、preflop 54(58-4)、flop 28
    incs = sorted(round(c) for (_, _, _, c) in bacts)
    assert incs == [4, 28, 54], (incs, bacts)
    # 覆盖对比:stack 只抓到 preflop-54 + flop-28(漏了 preflop-4)→ 应 bet_only=1
    fake_stack = [ChipAction(seat=1, street="preflop", chips_in=54, t=3.0),
                  ChipAction(seat=1, street="flop", chips_in=28, t=6.0)]
    cov = compare_coverage(fake_stack, bacts, tol=2.0, t_tol=3.0)
    assert cov["both_agree"] == 2 and cov["bet_only"] == 1 and cov["stack_only"] == 0, cov
    print(f"✅ 融合核:下注区反推动作 {incs};覆盖对比 {cov}(下注区补回 stack 漏的 1 个=bet_only)")

    # BUG1 ante 排除:4 座 @t1 各 -4(ante 簇)+ seat0 @t5 真 limp -4 → 簇排除、limp 留
    ss_ante = {
        0: [(0, 100), (0.5, 100), (1, 96), (1.5, 96), (5, 92), (5.5, 92)],  # ante@1 + limp@5
        1: [(0, 100), (0.5, 100), (1, 96), (1.5, 96)],
        2: [(0, 100), (0.5, 100), (1, 96), (1.5, 96)],
        3: [(0, 100), (0.5, 100), (1, 96), (1.5, 96)],
    }
    res_a = reconstruct(ss_ante, [(0, 0)], {"ante": 4, "tol": 2.0, "pot": 0, "seats": [0, 1, 2, 3]})
    vol = [(a.seat, round(a.chips_in), round(a.t)) for a in res_a.actions]
    assert vol == [(0, 4, 5)], vol  # 只剩 seat0 的真 limp,4 笔 ante 被排除
    print(f"✅ BUG1 ante 排除:4 笔 ante 簇剔除,仅留真 limp {vol}")

    # BUG2 all-in 标记:seat0 stack 稳 300,读取层标记 @t5(显胜率%)→ all-in 反解 300
    res_ai = reconstruct({0: [(0, 300), (1, 300)]}, [(0, 0)],
                         {"tol": 2.0, "pot": 0, "seats": [0]}, allin_marks={0: [5.0]})
    ai = [(a.atype, round(a.chips_in)) for a in res_ai.actions]
    assert ("all_in", 300) in ai, ai
    print(f"✅ BUG2 all-in 标记:胜率%标记 → 反解 all-in {ai}")

    # §19 call-to 候选:下注区累计 4→58(seat 先 limp 后跟到58)→ 候选含 58(匹配用户"跟到58"口径)
    cands = bet_callto_candidates(
        {6: [(0, None), (1, 4), (2, 4), (3, 58), (4, 58)]}, [(0, 0)], {"tol": 2.0})
    vals = sorted(v for (_, v) in cands)
    assert vals == [4.0, 58.0], vals  # call-to 候选 = 4 和 58(非增量 54)
    print(f"✅ §19 call-to 候选:{cands}(含 58=跟到额,治增量假象 + 补 all-in)")


if __name__ == "__main__":
    _self_test()
