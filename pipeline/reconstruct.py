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
    chips_in: float          # 本次投入(= stack 跌幅,增量)
    t: float
    atype: str = "?"         # 规则推:call/bet/raise/all_in
    confidence: float = 1.0  # 局部恒等式对上=1.0,缺校验<1
    to_amount: float = 0.0   # 该座本街累计投入(call-to/raise-to,= 用户标注口径)


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
    allin_marks: optional {seat: [t, ...]} 识别层标记的 all-in 时刻(来源无关的消费接口)
                 → 该座 all-in,金额=标记前最后已知 stack。
                 ⚠️ 来源:原 % 信号已废(2026-06-09:% 非 all-in 必出,见 R15 修正);
                 此接口休眠,待将来真持久 all-in 桩(真%-区 / fold_area "ALL IN" 字形)接入喂它。
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
    rise_ts = []  # 派彩 rise 时刻(结算锚:首个 rise = 本手结算开始,§19.12)
    for s in seats:
        plats = fuse_plateaus(stack_series.get(s, []), tol=tol)
        drops, _rises = detect_drops(plats)
        last_t = plats[-1][0] if plats else None
        last_v = plats[-1][1] if plats else None
        for (t, chips) in drops:
            is_allin = (last_v is not None and last_v <= tol and t == last_t)
            raw.append((t, s, chips, is_allin))
        # 🔧 §19.12+ 结算锚【只认从正持有上升的 rise】= 真派彩;从 ≈0 爬起(0→X)= 补码/重买/
        #   读 artifact(如兜底误补起手 0),**不锚结算** —— 否则手中段一个假 0→X 把 cutoff
        #   拉进手里、误删真实下注(实测 hand5:兜底补 seat0 起手0 → rise 前移到228 → turn74@242 被吞)。
        #   ⚠️ 代价:全下赢家若起于 0(0→派彩)也被排除该锚 → 该手少一道结算抑制(precision 微险,recall 安全)。
        for i in range(1, len(plats)):
            prev_v, cur_v = plats[i - 1][1], plats[i][1]
            if cur_v - prev_v > tol:
                res.notes.append(f"seat{s}@{plats[i][0]}: stack 涨 {cur_v - prev_v:.0f}(派彩/补码,非动作)")
                if prev_v > tol:  # 仅从正持有上升才锚结算
                    rise_ts.append(plats[i][0])

    # 🔧 BUG1+精度修:强制注簇排除 —— 手起始的强制投入(已在 res.forced),不计自愿。
    #    含 ante(各≈ante)+ SB 合并(ante+sb)+ BB 合并(ante+bb)。
    #    关键:BB 一次扣 ante+bb(如 4+4=8),ante 簇只排≈ante → 8 漏排 → 误判"bet 8"假阳(§19.6)。
    ante_marks = set()  # {(seat, round(t,1))}
    if ante > 0:
        forced_amts = [ante]
        if config.get("sb", 0):
            forced_amts.append(ante + config["sb"])   # SB 合并 = ante + 小盲
        if config.get("bb", 0):
            forced_amts.append(ante + config["bb"])    # BB 合并 = ante + 大盲
        ante_ts = sorted(t for (t, s, c, _) in raw if abs(c - ante) <= tol)
        if ante_ts:
            win = config.get("ante_cluster_win", 2.0)       # 检测窗:确认有 ante 簇
            sim_win = config.get("ante_sim_win", 1.0)        # 排除窗:只剔【同时刻】那批
            min_n = config.get("min_ante_seats", 3)
            t0 = ante_ts[0]
            if sum(1 for t in ante_ts if t - t0 <= win) >= min_n:  # 确认 ante 簇(够多座 ~ante)
                # 🔧 §19.12 ante==BB 死结:1/2/2 桌 ante=BB=limp=2,旧 t0+win 窗把紧跟的 limp
                #   误当 ante 剔掉 → recall 崩。修:antes 同帧齐跌、limp 逐个轮流(晚>sim_win),
                #   排除窗收到 sim_win + 每座只剔【最早】一笔(ante 每座仅一次,后续同额=limp 应留)。
                seen_seat = set()
                ante_marks = set()
                for (t, s, c, _) in sorted(raw, key=lambda e: e[0]):
                    if t0 <= t <= t0 + sim_win and s not in seen_seat \
                            and any(abs(c - fa) <= tol for fa in forced_amts):
                        ante_marks.add((s, round(t, 1)))
                        seen_seat.add(s)
                res.notes.append(f"强制注簇排除 {len(ante_marks)} 笔(@t≈{t0:.1f},含 ante{ante}/SB{ante+config.get('sb',0)}/BB{ante+config.get('bb',0)})")
    events = [(t, s, c, a) for (t, s, c, a) in raw if (s, round(t, 1)) not in ante_marks]
    events.sort(key=lambda e: e[0])  # 时间序
    cur_bet = {}  # street -> 当前最大单家投入
    contrib = {}  # (seat, street) -> 该座本街累计投入(算 call-to/raise-to)
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
        contrib[(s, st)] = contrib.get((s, st), 0.0) + chips  # 累计 → call-to
        act = ChipAction(seat=s, street=st, chips_in=chips, t=t, atype=atype,
                         to_amount=contrib[(s, st)])
        if bet_reads and s in bet_reads:  # 局部恒等式三腿校验
            near = [bv for (bt, bv) in bet_reads[s] if abs(bt - t) <= config.get("t_tol", 1.0)]
            if near:
                act.confidence = 1.0 if min(abs(bv - chips) for bv in near) <= tol else 0.5
                if act.confidence < 1.0:
                    res.notes.append(f"seat{s}@{t}: stack跌{chips}≠下注区{near} → 低置信")
        res.actions.append(act)

    # 🔧 T140 全下封盘线抑制(规则:被标 all-in 的座此后不再行动):某座有 all-in 标记(allin_marks)
    #    后,其 stack 跌幅 = 该座全下的【晚回声】(牌型/横幅盖住,直到结算才显跌后值)或结算
    #    噪声,**非新下注** → 删。全下本身随后由下方 allin_marks 路径在【决策街=首标记时刻】补回。
    #    **按座**:只删"被标 all-in 座"自己的晚跌;不动其他可读座。修 all-in 街错 FP。
    #    (注:原经由 % 信号喂标记,% 已废;现接口休眠待真持久 all-in 桩接入。)
    if allin_marks:
        first_mark = {s: min(ts) for s, ts in allin_marks.items() if ts}
        margin = config.get("lock_margin", 0.5)
        n0 = len(res.actions)
        res.actions = [a for a in res.actions
                       if not (a.seat in first_mark and a.t > first_mark[a.seat] + margin)]
        if len(res.actions) < n0:
            res.notes.append(f"全下封盘线抑制 {n0 - len(res.actions)} 笔(被标all-in座标记后的晚回声/结算,非下注)")

    # 🔧 BUG2 修:all-in 反解 —— 识别层给某座打 all-in 标记(来源无关)→ all-in。
    #    金额 = 标记前最后已知 stack(他把剩余全推);若该座该街已有动作捕获则跳过(防重复)。
    #    (来源:原 % 信号已废 2026-06-09;接口休眠待真持久 all-in 桩接入。)
    if allin_marks:
        t_tol = config.get("t_tol", 1.0)
        for s, ts in allin_marks.items():
            plats = fuse_plateaus(stack_series.get(s, []), tol=tol)
            for tm in sorted(ts):
                # 🔧 一座一手只 all-in 一次:%标记跨多帧持续 → 旧 t_tol 去重漏(标记间隔>t_tol),
                #   同手同座产 524×3 假阳。改:该座已有 all-in 即跳(全下后不再行动)。
                if any(a.seat == s and a.atype == "all_in" for a in res.actions):
                    continue
                prior = [(t, v) for (t, v) in plats if t <= tm + t_tol]
                if not prior or prior[-1][1] <= tol:
                    continue  # 无前置 stack 或已≈0(归零型 all-in 已被 is_allin 抓)
                if any(a.seat == s and abs(a.t - tm) <= t_tol for a in res.actions):
                    continue  # 该座该时刻已有动作
                st = street_at(tm, boundaries)
                res.actions.append(ChipAction(seat=s, street=st, chips_in=prior[-1][1],
                                              t=tm, atype="all_in", confidence=0.7,
                                              to_amount=prior[-1][1]))
                res.notes.append(f"seat{s}@{tm:.1f}: all-in 标记 → 反解投入≈{prior[-1][1]:.0f}")

    # 🔧 §19.12 结算抑制:首个派彩 rise = 本手结算开始 → 其前 settle_guard 秒起的"动作"判结算
    #   噪声(showdown 揭示期的 stack 抖动/重复%标记),抑制。锚到 rise(真实结算点),
    #   不再靠窗末 t1(按钮 seg 后 t1=下一手起点,离结算>2s → 旧 settle-win 失准、precision 回归)。
    settle_guard = config.get("settle_guard", 2.0)
    if rise_ts and settle_guard >= 0:
        cutoff = min(rise_ts) - settle_guard
        n0 = len(res.actions)
        res.actions = [a for a in res.actions if a.t < cutoff]
        if len(res.actions) < n0:
            res.notes.append(f"结算抑制 {n0 - len(res.actions)} 笔(首派彩 {min(rise_ts):.1f} 前 {settle_guard}s 起判结算噪声)")

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
def hand_ends_from_win(win_series, merge=6.0, min_win=0.0):
    """win_amount(+xx 结算)非空时刻 → 聚类成手末事件 [t,...]。
    +xx=结算=手末:**普适**(翻牌前结束的手也有,公共牌 reset 标不出),且 stack 误读不会凭空产 +xx。
    min_win:滤掉 < min_win 的小额 +xx(治某些桌 win_amount ROI 系统误读出 +3/+33 噪声 + 小额边池;
            真结算赢家拿走的额远大于此,主赢家的大额 +xx 仍标得出手末)。
    merge:同一结算的多帧/散开(动画+分边池揭示)合并窗;大桌结算散开需调大。
    实测:170343 干净(merge6/min0);5/10/10 桌噪声大(需 merge~12/min~50)。返回每簇首个时刻。"""
    ts = sorted(t for obs in win_series.values() for (t, v) in obs if v is not None and v >= min_win)
    if not ts:
        return []
    ends = [ts[0]]
    for t in ts[1:]:
        if t - ends[-1] > merge:
            ends.append(t)
    return ends


def hand_starts_from_button(button_series, merge=3.0):
    """button-seat 变到【新非None座】= 新手开始(D 纯白高对比、亮度检测、不 OCR)。
    实测(段2)最干净的切手信号:catch 所有手含无翻牌手(公共牌漏)、不吃 OCR 垃圾(+xx 漏)。
    忽略 None 过渡(发牌瞬间按钮短暂消失);merge 防同一移座多帧重复。返回 hand-start 时刻。"""
    starts, last = [], None
    for (t, s) in sorted(button_series):
        if s is None:
            continue
        if s != last:
            if not starts or t - starts[-1] > merge:
                starts.append(t)
            last = s
    return starts


def button_moves_monotonic(button_series, num_seats=8, min_hold=3.0, max_skip=4):
    """D 按钮移座 → hand-start 时刻,带两道【误读过滤】(T139):
    (a) **去抖**:某座持有 < min_hold 秒 = 瞬时闪读(误读到别座),丢;
    (b) **顺时针单调**:庄家钮每手只【顺时针前进】到下一个有人座(跳过空座);
        新座 = (旧座 + k) % num_seats,k∈[1,max_skip] 才合法;倒退/远跳(如误读闪到
        对面再跳回)→ 拒。要求 seat_index 顺时针排布(party_poker_8 已是:
        0底→1/2/3左→4顶→5/6/7右)。
    返回有效移座时刻 [t,...](= hand starts)。纯逻辑,可单测。"""
    # 压缩成段 (t_start, seat, t_end):同座连续(None 跳过、不断段)
    segs = []
    for (t, s) in sorted(button_series):
        if s is None:
            continue
        if segs and segs[-1][1] == s:
            segs[-1][2] = t
        else:
            segs.append([t, s, t])
    # (a) 去抖:持有 ≥ min_hold(只一段则不丢)
    held = [(t0, s) for (t0, s, t1) in segs if t1 - t0 >= min_hold or len(segs) == 1]
    # (b) 顺时针单调
    out, cur = [], None
    for (t, s) in held:
        if cur is None:
            out.append(t); cur = s; continue
        if 1 <= (s - cur) % num_seats <= max_skip:   # 合法顺时针前进
            out.append(t); cur = s
        # else 倒退/远跳 = 误读 → 忽略(cur 不变)
    return out


def button_move_online(confirmed, pending, pending_count, btn_now,
                       num_seats=8, debounce=2, max_skip=4):
    """`button_moves_monotonic` 的【在线流式】版(live 切手用):给当前状态 + 本帧按钮读数,
    返回 (moved, new_confirmed, new_pending, new_pending_count)。

    与 batch 版同两道误读过滤,但去抖改成【连续 debounce 帧读到同一顺时针新座才提交】
    (batch 是回看持有时长;live 看不到未来,用帧计数):
      - btn_now=None(本帧没看清 D)→ 不动状态;
      - 回到已确认座 → 清候选;
      - 非顺时针(倒退/远跳,(new-cur)%num_seats∉[1,max_skip])→ 当误读忽略;
      - 顺时针新座:同座累计,≥debounce → moved=True、confirmed 推进。
    纯逻辑、无 cv2,可 Linux 单测。"""
    if btn_now is None:
        return False, confirmed, pending, pending_count
    if confirmed is None:
        return False, btn_now, None, 0                       # 首次锚定,不算移动
    if btn_now == confirmed:
        return False, confirmed, None, 0                     # 回到已确认 → 清候选
    if not (1 <= (btn_now - confirmed) % num_seats <= max_skip):
        return False, confirmed, pending, pending_count      # 倒退/远跳 = 误读,忽略
    # 顺时针候选:去抖累积
    if btn_now == pending:
        pending_count += 1
    else:
        pending, pending_count = btn_now, 1
    if pending_count >= debounce:
        return True, btn_now, None, 0                        # 持稳 → 提交移动 = 换手
    return False, confirmed, pending, pending_count


def blinds_from_button(button, active_seats, num_seats):
    """从按钮在【活跃集】(card_marker phash 判在手的座)里顺时针定 SB/BB 座(2026-06-06)。
    **关键:走活跃集的下一位/下下一位,不盲目 D+1/D+2**——占座≠发牌(带入审核/坐下未发等),
    必须跳过非活跃座。
    - 3+ 活跃:SB=按钮后第一个活跃座、BB=第二个;
    - 单挑(活跃=2):按钮=SB、对家=BB(德州规则);
    - 活跃<2 或 button=None:返 (None, None)。
    返回 (sb_seat, bb_seat)。纯逻辑,可 Linux 单测。ante 由 caller 派给全活跃集。"""
    act = set(active_seats)
    if button is None or len(act) < 2:
        return None, None
    after_active = [(button + k) % num_seats for k in range(1, num_seats)
                    if (button + k) % num_seats in act]
    if len(act) == 2:
        return button, (after_active[0] if after_active else None)   # 单挑:按钮=SB
    sb = after_active[0] if len(after_active) >= 1 else None
    bb = after_active[1] if len(after_active) >= 2 else None
    return sb, bb


def reconcile_underread_amount(action, amount, stack_delta, margin=8, ratio=2):
    """下注区 amount 漏读兜底(2026-06-06,用户脑内复盘机制):**当前街最后一个行动者跟注**时,
    筹码一进池街就翻,下注区显示真额的时间窗极短 → 抓帧常卡在该玩家自己盲注筹码(2/4)那一瞬,
    读成盲注而非真跟注额。stack 跌幅(净投入)不受此窗口影响 → 当 stack_delta 明显 > 下注区读数
    (差 ≥margin 且 ≥ratio×)即判漏读,用 stack_delta 当 amount,返 (new_amount, reason);
    否则原样返 (amount, None)。仅 call/bet/raise、stack 可信(>0)。纯逻辑,可 Linux 单测。

    实证:可乐(SB2)跟allin净投25读成2 / 神啊(BB4)跟到34净投30读成4(用户翻牌谱钉死)。"""
    if action not in ("call", "bet", "raise"):
        return amount, None
    if stack_delta is None or stack_delta <= 0:
        return amount, None
    if amount is None:
        # 2026-06-11 amount 抓帧时机修:下注区金额只闪几帧、动作 overlay 往往 outlast 它 → 记录动作
        # 那刻金额已清=None(label 验:recipe 本身~100%、是时机问题)。stack 跌幅(净投入,持久可靠
        # ~100%)即本动作投入=amount。"下注区瞬态、stack跌幅持久"(见 digit-ocr-stack-recipe)。
        return stack_delta, f"amount None→{stack_delta}(下注区瞬态漏抓,stack跌幅兜底)"
    if stack_delta - amount >= margin and stack_delta >= ratio * amount:
        return stack_delta, f"amount {amount}→{stack_delta}(下注区last-actor短窗漏读,stack跌幅兜底)"
    return amount, None


def pot_debounce_step(state, amount, run_th=2):
    """pot 防抖(2026-06-10):同值连续 ≥run_th 帧才接受 → 杀单帧动画毛刺/尖峰(治 spike-lock:
    单帧 13353 永远到不了 2 帧、不被接受)。None=本帧无读(配方在动画帧返 None)→ 不打断游程、
    不接受(hold 上值)。state={'pending','count'};返回 accepted(float)或 None。纯逻辑,Linux 单测。"""
    if amount is None:
        return None
    if amount == state.get('pending'):
        state['count'] = state.get('count', 0) + 1
    else:
        state['pending'] = amount
        state['count'] = 1
    return amount if state['count'] >= run_th else None


def reconstruct_hand_chips(initial, final, pot=None, sb=0, bb=0, ante=0):
    """#226 筹码级重建(2026-06-06):从手末全座 stack 端点重建每手 per-seat 净额 + 赢家 + rake。

    依据(实测验证):per-hand stack 端点(`player_stacks_initial`/`final`,全座)是可靠桩
    (本会话 24/25 手守恒到 rake 级),per-action 读取噪声(~53%)不影响此层。
    端点**只能可靠定**:① 每人净额(final−initial)② 赢家(净额>0)③ rake(=−Σ净额)
    ④ 输家投入(=净损,精确)。**赢家投入/绝对底池端点定不了**(winner_contrib∈[0,初始]),
    只能用 pot 读交叉验证 + sanity 标记。纯逻辑,可 Linux 单测。

    initial/final: {seat:int → stack:float}。pot:读到的底池(可选,仅交叉验证)。
    返回 dict:net{座→净额} / winners / losers / rake / sum_loss / conservation_ok /
             pot_read / pot_plausible(通过sanity才给) / flags。
    """
    seats = sorted(set(initial) & set(final))
    flags = []
    if not seats:
        return {"net": {}, "winners": [], "losers": [], "rake": None, "sum_loss": 0.0,
                "conservation_ok": False, "pot_read": pot, "pot_plausible": None,
                "flags": ["no_common_seats"]}
    if (set(initial) | set(final)) - set(seats):
        flags.append("partial_seats")
    net = {s: round(final[s] - initial[s], 1) for s in seats}
    sum_net = sum(net.values())
    tol = max(2.0, 0.5 * bb)                       # 净额噪声容忍(< 半 BB 当 0)
    winners = sorted([s for s in seats if net[s] > tol])
    losers = sorted([s for s in seats if net[s] < -tol])
    rake = round(-sum_net, 1)
    sum_loss = round(sum(-net[s] for s in losers), 1)
    # 守恒:Σ净额应是小负(=rake);大正=rebuy/买入,过大负=离场/快照错
    rake_ceiling = max(3 * bb, 0.08 * (pot or 0), 30)
    conservation_ok = (-rake_ceiling <= sum_net <= tol)
    if sum_net > tol:
        flags.append(f"rebuy/chips_added(Σnet={sum_net:+.0f})")
    elif sum_net < -rake_ceiling:
        flags.append(f"excess_loss(Σnet={sum_net:+.0f})")
    if not winners:
        flags.append("no_winner")
    elif len(winners) > 1:
        flags.append(f"split/multi_winner{winners}")
    # pot 交叉验证:端点定不了绝对底池,但赢家匹配投入 ≤ 输家总投入 → pot ≲ 2×输家投入
    pot_plausible = None
    if pot and len(winners) == 1:
        if pot > 2.5 * max(sum_loss, 1):
            flags.append(f"pot_suspect(读{pot:.0f}≫2.5×输家投入{sum_loss:.0f}→疑超读)")
        else:
            pot_plausible = pot
    return {"net": net, "winners": winners, "losers": losers, "rake": rake,
            "sum_loss": sum_loss, "conservation_ok": conservation_ok,
            "pot_read": pot, "pot_plausible": pot_plausible, "flags": flags}


def veto_actions_by_endpoint(actions, endpoint, final, bb=0):
    """#226 端点否决(2026-06):用端点(唯一真值锚)否决逐动作候选里的两类噪声。

    端点层只能可靠定 net/winners(见 reconstruct_hand_chips),但这两样足以**反证**
    两类高频 per-action 假事件(2026-06-15 全量审计:摊牌期假弃牌≈1/4 手、假全下≈5%):
      ① **假全下**:某座记了 all_in,但端点显示它【既非赢家(net≤tol)又留着筹码(final>tol)】
         → 全下=投光,留着筹码说明没全下 → 否决。(实测罗湖net-545终栈860 / 东胜net-39终栈50)
      ② **赢家误弃**:某座记了 fold,但端点显示它是赢家(net>tol) → 赢家不可能弃 → 否决该 fold。
         (实测水上 net+506 却被记弃牌——摊牌亮牌时牌背消失被误判,见摊牌闸 #235)
    (③ 漏抓真全下=端点显示爆栈/缺座但无 all_in 记录 → 需 stackzero 配合,留下一步)

    actions:[{"seat":int,"action_type":str,"amount":?,...}];endpoint:reconstruct_hand_chips 返回;
    final:{座→终栈}。返回 {"actions":保留的, "vetoed":[带 veto_reason], "corrected":[纠正记录]}。
    纯逻辑,Linux 可单测。否决是【删假】不是【造真】:只移除矛盾候选,不凭空补事件(那是规则③/求解器)。
    """
    net = endpoint.get("net", {})
    winners = set(endpoint.get("winners", []))
    tol = max(2.0, 0.5 * bb)
    kept, vetoed, corrected = [], [], []
    for a in actions:
        s, at = a.get("seat"), a.get("action_type")
        if at == "all_in" and s in net and net[s] <= tol and final.get(s, 0) > tol:
            vetoed.append({**a, "veto_reason":
                           f"false_all_in(net={net[s]:+.0f},final={final.get(s):.0f}>0=未投光)"})
            continue
        if at == "fold" and s in winners:
            vetoed.append({**a, "veto_reason": f"winner_marked_fold(net={net[s]:+.0f}>0=赢家不可能弃)"})
            corrected.append({"seat": s, "from": "fold", "to": "played_to_showdown",
                              "by": f"endpoint_net={net[s]:+.0f}"})
            continue
        kept.append(a)
    return {"actions": kept, "vetoed": vetoed, "corrected": corrected}


def corroborate_boundaries(candidates, cluster_win=6.0, anchor="button", min_signals=2):
    """多信号交叉印证切手(T139):candidates=[(t, signal_name),...]。
    按时间聚类(相邻 ≤cluster_win 归一簇),一簇是【真边界】当:
      含 anchor 信号(每手必移、权威)  OR  ≥min_signals 个【不同】信号印证。
    → 孤立单信号(如公共牌误 reset)被否决;按钮缺时 ≥2 其他信号可补漏。
    返回真边界时刻(每簇取首信号时刻),升序。纯逻辑,可单测。
    **降级**:若候选里【完全没有 anchor 信号】(如该桌无按钮ROI/全程漏检),自动退回
    宽松(min_signals=1=旧并集),避免"只有公共牌一种信号"时全被否决 → 切不出手。"""
    if not candidates:
        return []
    eff_min = min_signals if any(sig == anchor for _, sig in candidates) else 1
    clusters = []  # [first_t, set(signals), last_t]
    for t, sig in sorted(candidates, key=lambda x: x[0]):
        if clusters and t - clusters[-1][2] <= cluster_win:
            clusters[-1][1].add(sig)
            clusters[-1][2] = t
        else:
            clusters.append([t, {sig}, t])
    return [ft for ft, sigs, _ in clusters if anchor in sigs or len(sigs) >= eff_min]


def segment_hands(stack_series, community_series, config):
    """检测手边界 → [(t_start, t_end)] 每手窗口。**T139 交叉印证组合器**:
    汇集多信号候选(按钮移座[anchor,已顺时针单调过滤] / 公共牌reset / 派彩大涨 / ante簇
    / 可选 win-phash),交叉印证(含按钮 OR ≥2 信号)才算真边界 → 否决孤立假信号
    (如公共牌误 reset 把 1 手切碎)。按钮缺时多信号补漏。
    config: hand_starts(=button_moves_monotonic 产的时刻) · win_ends(可选,+xx结算) ·
            payout_th/ante/boundary_merge/min_corroborate。"""
    allt = [t for obs in stack_series.values() for (t, _) in obs] + [t for (t, _) in community_series]
    if not allt:
        return []
    t0, tN = min(allt), max(allt)
    tol = config.get("tol", 2.0)
    payout_th = config.get("payout_th", max(config.get("bb", 4) * 10, 50))
    ante = config.get("ante", 0)
    cand = []
    # ① 按钮移座(anchor):caller 传 monotonic-filtered hand_starts
    for s in (config.get("hand_starts") or []):
        cand.append((s, "button"))
    # ② 公共牌 reset(n 下降)
    for k in range(1, len(community_series)):
        if community_series[k][1] < community_series[k - 1][1]:
            cand.append((community_series[k][0], "community"))
    # ③ 派彩大涨(任一座平台跳 > payout_th)
    for obs in stack_series.values():
        plats = fuse_plateaus(obs, tol=tol)
        for i in range(1, len(plats)):
            if plats[i][1] - plats[i - 1][1] > payout_th:
                cand.append((plats[i][0], "payout"))
    # ④ ante 簇:相近时间多座各跌 ≈ante
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
                cand.append((ad[i], "ante"))
            i = j
    # ⑤ +xx 结算时刻(win-phash,T130;hand-end≈边界,与按钮 hand-start 聚同簇)
    for w in (config.get("win_ends") or []):
        cand.append((w, "win"))
    # 切手:**按钮权威**——按钮在场时【只用按钮边界】(已 monotonic+debounce),其余信号
    #   不去切按钮确认的手(防 payout/公共牌抖动把按钮确认的 1 手从中间错切,= T139 回归点)。
    #   按钮缺席时才用多信号交叉印证(≥2)补位。按钮漏整手的 gap-fill 留后续(需 gap 长度阈,慎)。
    button_bounds = sorted(t for (t, sig) in cand if sig == "button")
    if button_bounds:
        bounds = button_bounds
    else:
        bounds = corroborate_boundaries(
            cand, cluster_win=config.get("boundary_merge", 6.0),
            anchor="button", min_signals=config.get("min_corroborate", 2))
    cuts = sorted(set([t0] + [b for b in bounds if t0 < b < tN] + [tN + 0.01]))
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

    # T130 win-分段:+xx 结算时刻 → 手末 → 切手(治过度切分 + 翻牌前手)
    win_series = {0: [(10, None), (26, 1330), (26.5, 1330)], 1: [(68, 1117)], 4: [(110, None), (110, 192)]}
    ends = hand_ends_from_win(win_series, merge=6.0)
    assert ends == [26, 68, 110], ends  # 3 个结算事件
    ss_w = {0: [(0, 100), (90, 96)], 1: [(0, 100), (120, 96)]}
    wins_w = segment_hands(ss_w, [], {"win_ends": [26, 68, 110], "tol": 2.0})
    assert len(wins_w) == 4 and wins_w[0][1] == 26, wins_w  # [0,26][26,68][68,110][110,120]
    print(f"✅ T130 win-分段:结算 {ends} → {len(wins_w)} 手窗 {[(round(a),round(b)) for a,b in wins_w]}")

    # 精度修:BB 合并强制注(ante+bb=8)排除 + to_amount(call-to 口径)
    ss_bb = {
        0: [(0, 100), (0.5, 100), (1, 96), (1.5, 96)],   # ante 4
        1: [(0, 100), (0.5, 100), (1, 96), (1.5, 96)],   # ante 4
        2: [(0, 100), (0.5, 100), (1, 96), (1.5, 96)],   # ante 4
        3: [(0, 200), (0.5, 200), (1, 192), (1.5, 192), (5, 140), (5.5, 140)],  # BB ante+bb=8@1 + raise52@5
    }
    res_bb = reconstruct(ss_bb, [(0, 0)], {"ante": 4, "sb": 2, "bb": 4, "tol": 2, "pot": 0, "seats": [0, 1, 2, 3]})
    acts_bb = [(a.seat, round(a.chips_in), round(a.to_amount)) for a in res_bb.actions]
    assert acts_bb == [(3, 52, 52)], acts_bb  # BB 的 8(ante+bb)排除,只留真 raise 52;to_amount=52
    print(f"✅ 精度修:BB 合并post(8)排除,仅留真动作 + to_amount {acts_bb}")

    # §19.12 ① ante==BB 死结:ante=BB=10,limp10 紧跟(@2.5,在旧 win=2 窗内但晚于 sim_win)
    #   → 旧码误剔 limp,新码(sim_win+每座一笔)保留。
    ss_knot = {
        0: [(0, 200), (0.5, 200), (1, 190), (1.5, 190), (2.5, 180), (3, 180)],  # ante10@1 + limp10@2.5
        1: [(0, 200), (0.5, 200), (1, 190), (1.5, 190)],
        2: [(0, 200), (0.5, 200), (1, 190), (1.5, 190)],
        3: [(0, 200), (0.5, 200), (1, 190), (1.5, 190)],
    }
    res_k = reconstruct(ss_knot, [(0, 0)], {"ante": 10, "bb": 10, "tol": 2.0, "pot": 0, "seats": [0, 1, 2, 3]})
    volk = [(a.seat, round(a.chips_in)) for a in res_k.actions]
    assert volk == [(0, 10)], volk  # 4 笔 ante 同时刻剔除,seat0 晚到的 limp10 保留
    print(f"✅ §19.12① ante==BB:同时刻簇剔 ante、保留紧跟 limp {volk}")

    # §19.12 ② all-in 去重:%标记跨多帧(5/6.5/8,间隔>t_tol)→ 同座只产 1 笔 all-in(治 524×3)
    res_dd = reconstruct({0: [(0, 300), (1, 300), (2, 300)]}, [(0, 0)],
                         {"tol": 2.0, "pot": 0, "seats": [0]}, allin_marks={0: [5.0, 6.5, 8.0]})
    ndd = [a for a in res_dd.actions if a.atype == "all_in"]
    assert len(ndd) == 1, ndd
    print(f"✅ §19.12② all-in 去重:3 帧%标记 → 1 笔 all-in(投入{ndd[0].chips_in:.0f})")

    # §19.12 ③ 结算抑制:bet100@2(真)+ 噪声 drop50@7 + 派彩 rise@8 → cutoff=8-2=6,噪声删、bet 留
    ss_settle = {0: [(0, 300), (1, 300), (2, 200), (3, 200), (7, 150), (7.5, 150), (8, 500), (9, 500)]}
    res_s = reconstruct(ss_settle, [(0, 0)], {"tol": 2.0, "pot": 0, "seats": [0], "settle_guard": 2.0})
    acts_s = [(round(a.chips_in), round(a.t)) for a in res_s.actions]
    assert acts_s == [(100, 2)], acts_s  # 首派彩@8 前 2s 起判结算噪声 → 删 @7,留 @2
    print(f"✅ §19.12③ 结算抑制:锚首派彩,删结算期噪声 drop,留真 bet {acts_s}")

    # §19.12+ 回归:手中段【0→X 假 rise】(某座起手补0/重买/读 artifact)绝不锚结算 →
    #   不得把 cutoff 拉进手中误删后面真实下注(实测 hand5 seat6 turn74 被吞的根因)。
    ss_zero_rise = {
        0: [(0, 500), (1, 500), (10, 400), (11, 400)],   # 真 bet 100 @10
        1: [(0, 0), (1, 0), (5, 200), (6, 200)],          # 0→200 @5 = 补码/重买(prev=0,不锚)
    }
    res_z = reconstruct(ss_zero_rise, [(0, 0)], {"tol": 2.0, "pot": 0, "seats": [0, 1], "settle_guard": 2.0})
    acts_z = [(round(a.chips_in), round(a.t), a.seat) for a in res_z.actions]
    assert acts_z == [(100, 10, 0)], acts_z  # 修前:0→200@5 把 cutoff 拉到 3 → bet@10 被误删 → []
    print(f"✅ §19.12+ 0→X假rise不锚结算:手中段补0 rise 不误删真 bet {acts_z}")

    # T132 按钮切手:button-seat 移座 → hand-start(忽略 None 过渡)
    btn = [(0, 6), (1, None), (5, 7), (6, 7), (10, None), (11, 0), (20, 0), (21, None), (22, 1)]
    starts = hand_starts_from_button(btn, merge=3.0)
    assert starts == [0, 5, 11, 22], starts  # 6→7→0→1 四手起点(None 不算、同座不重复、间隔>merge)
    ss_b = {0: [(0, 100), (30, 96)]}
    wins_b = segment_hands(ss_b, [], {"hand_starts": [0, 2, 11, 22], "tol": 2.0, "boundary_merge": 1.0})
    assert len(wins_b) == 4, wins_b  # 4 手窗
    print(f"✅ T132 按钮切手:button移座 {starts} → {len(wins_b)} 手窗")

    # 2026-06-06 在线去抖 button_move_online(live 切手):喂逐帧读数,debounce=2
    st = (None, None, 0)  # (confirmed, pending, pending_count)
    cuts = []
    # 6→6(锚)→7,7(持稳2帧=切)→7→None→0,0(切)→倒退6(拒)→0→1(仅1帧不切)
    for i, b in enumerate([6, 6, 7, 7, 7, None, 0, 0, 6, 0, 1]):
        moved, *st = button_move_online(st[0], st[1], st[2], b, num_seats=8, debounce=2)
        if moved:
            cuts.append((i, st[0]))
    assert cuts == [(3, 7), (7, 0)], cuts  # 仅 7 与 0 各持稳2帧时切;6→7 第2帧、0 第2帧;倒退6被拒
    # 单帧闪到顺时针新座(只1帧)不切;None 不动状态
    m1, c1, p1, pc1 = button_move_online(5, None, 0, 6, num_seats=8, debounce=2)
    assert m1 is False and c1 == 5 and p1 == 6 and pc1 == 1, (m1, c1, p1, pc1)
    m2, *_ = button_move_online(5, 6, 1, 6, num_seats=8, debounce=2)  # 第2帧 → 切
    assert m2 is True, (m2,)
    m3, c3, *_ = button_move_online(5, None, 0, 3, num_seats=8, debounce=2, max_skip=4)  # 5→3 倒退/远跳 %8=6>4 拒
    assert m3 is False and c3 == 5, (m3, c3)
    print("✅ button_move_online 在线去抖:顺时针持稳切/单帧不切/倒退拒/None不动")

    # 2026-06-06 #226 reconstruct_hand_chips(端点筹码级重建)——实测 4 手验证
    # 24f5(干净):MP(s4) 输296 / BTN(s7) 赢,pot 622 合理
    r = reconstruct_hand_chips(
        {0:359,1:314,2:865,3:144,4:545,5:630,6:164,7:760},
        {0:355,1:308,2:857,3:140,4:249,5:626,6:160,7:1074}, pot=622, sb=2, bb=4, ante=4)
    assert r["winners"] == [7] and r["net"][7] == 314 and r["net"][4] == -296, r
    assert r["rake"] == 12 and r["sum_loss"] == 326 and r["conservation_ok"], r
    assert r["pot_plausible"] == 622 and not r["flags"], r
    # 5e60(pot 超读):赢家净 +100 但 pot 读 1441 ≫ 2.5×输家 → pot_suspect,净额仍对
    r2 = reconstruct_hand_chips(
        {0:382,1:1586,2:391,3:887,4:706,5:431,6:512,7:1676},
        {0:378,1:1550,2:387,3:851,4:700,5:425,6:500,7:1776}, pot=1441, sb=2, bb=4, ante=4)
    assert r2["winners"] == [7] and r2["net"][7] == 100, r2
    assert r2["pot_plausible"] is None and any("pot_suspect" in f for f in r2["flags"]), r2
    # 0809(动作捕获错赢家=BB;端点纠正:真赢家是 s5 把把买顺)
    r3 = reconstruct_hand_chips(
        {0:498,1:1107,2:189,3:2129,4:760,5:286,6:1126,7:591},
        {0:494,1:1103,2:185,3:1945,4:752,5:486,6:1122,7:587}, pot=502, sb=2, bb=4, ante=4)
    assert r3["winners"] == [5] and r3["net"][5] == 200 and r3["net"][3] == -184, r3
    # rebuy 离群(Σnet 大正)→ conservation_ok False + flag
    r4 = reconstruct_hand_chips({0:100,1:200}, {0:96,1:473}, pot=50, sb=1, bb=2, ante=2)
    assert not r4["conservation_ok"] and any("rebuy" in f or "chips_added" in f for f in r4["flags"]), r4
    print("✅ reconstruct_hand_chips:净额/赢家/rake精确·pot超读flag·纠错赢家·rebuy异常")

    # 2026-06-06 amount 漏读兜底 reconcile_underread_amount
    assert reconcile_underread_amount("call", 2, 25) == (25, reconcile_underread_amount("call", 2, 25)[1])
    assert reconcile_underread_amount("call", 2, 25)[0] == 25      # 可乐:读2/跌25 → 兜25
    assert reconcile_underread_amount("call", 4, 30)[0] == 30      # 神啊:读4/跌30 → 兜30
    assert reconcile_underread_amount("call", 4, 4) == (4, None)   # 真BB跟注 amount=stack → 不动
    assert reconcile_underread_amount("raise", 40, 40) == (40, None)  # 一致 → 不动
    assert reconcile_underread_amount("call", 10, 14) == (10, None)   # 差4<margin8 → 不动(防stack噪声误兜)
    assert reconcile_underread_amount("call", 4, 7) == (4, None)      # 7<2×4 ratio不足 → 不动
    assert reconcile_underread_amount("fold", 0, 30) == (0, None)     # 非chip动作 → 不动
    assert reconcile_underread_amount("call", 4, None) == (4, None)   # stack空 → 不动(回退下注区)
    assert reconcile_underread_amount("call", None, 30) == (None, None)
    print("✅ reconcile_underread_amount:漏读兜stack跌幅/一致不动/小差不兜/ratio护栏/stack空回退")

    # 2026-06-06 blinds_from_button(活跃集走位定 SB/BB,跳空座)
    assert blinds_from_button(7, {0,1,2,3,4,5,6,7}, 8) == (0, 1)      # 满座:D7→SB0/BB1
    assert blinds_from_button(7, {1,2,3}, 8) == (1, 2)                # 跳空座0:D7→SB1/BB2
    assert blinds_from_button(2, {2,5,7}, 8) == (5, 7)                # D2→下一活跃5/下下一7
    assert blinds_from_button(3, {3,6}, 8) == (3, 6)                  # 单挑:D=SB3,对家BB6
    assert blinds_from_button(7, {7,0,4}, 8) == (0, 4)                # 环绕:D7→SB0/BB4(跳1235)
    assert blinds_from_button(5, {5}, 8) == (None, None)             # 活跃<2
    assert blinds_from_button(None, {1,2,3}, 8) == (None, None)      # 无按钮
    print("✅ blinds_from_button:满座/跳空座/环绕/单挑(D=SB)/活跃不足/无按钮")

    # T140 全下封盘线抑制(规则:%⟺该座all-in⟺此后不行动):显%座%后的晚跌=回声/结算,删;
    #   全下在决策街(首%)补;**按座**不动多人边池里可读的非全下座。
    comm_a = [(0, 0), (5, 3), (7, 4), (9, 5)]  # preflop[0,5) flop[5,7) turn[7,9) river[9,)
    cfg_a = {"seats": [0, 1], "bb": 4, "ante": 0, "pot": 1000, "tol": 2.0, "settle_guard": -1}
    # ① heads-up:limp@2 + 全下决策%@4(preflop),晚跌注册@8(river) → river回声删、全下补preflop
    r_a = reconstruct({0: [(0, 500), (.5, 500), (2, 496), (2.5, 496), (8, 0), (8.5, 0)],
                       1: [(0, 500), (.5, 500), (2, 496), (2.5, 496), (8.5, 4), (9, 4)]},
                      comm_a, cfg_a, allin_marks={0: [4.0]})
    assert not any(a.seat == 0 and a.street == "river" for a in r_a.actions), r_a.actions
    assert any(a.seat == 0 and a.atype == "all_in" and a.street == "preflop" for a in r_a.actions), r_a.actions
    # ② 多人边池:seat0全下(%@4,晚跌@8) + seat1翻牌真下注@6(可读无%)→ seat1不被误删
    r_b = reconstruct({0: [(0, 500), (2, 400), (2.5, 400), (8, 0), (8.5, 0)],
                       1: [(0, 1000), (.5, 1000), (6, 950), (6.5, 950)]},
                      comm_a, cfg_a, allin_marks={0: [4.0]})
    assert any(a.seat == 1 and a.t >= 6 for a in r_b.actions), r_b.actions  # 边池下注保留
    print("✅ T140 全下封盘线:晚回声删+全下补决策街;多人边池可读座保留")


if __name__ == "__main__":
    _self_test()
