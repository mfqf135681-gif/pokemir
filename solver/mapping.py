"""solver/mapping.py — 砖3:整环座位↔玩家映射(治 MAPPING_GAP 22 手)。

为什么严格值匹配有缺口:raw_data.player_stacks_initial(开手快照)与 post_ante 的
stack_before(注入时 _prev_stack)是【两次不同的读】——多数相等,但常差几个筹码
或单边缺读 → 精确相等漏 8-12% 座次,这些座的端点净额就丢了(MAPPING_GAP 病因)。

算法(纯逻辑,不复刻生产座次推导,防 harness/live 漂移):
  1. 候选图:|init[seat] − ante_stk[player]| ≤ tol → 候选边(双边都缺读=无边);
  2. 锚先落座:sb/bb/btn 座号 × hands.seats 的 SB/BB/BTN 名(确定性,优先级最高);
  3. 约束传播:迭代收"唯一候选"——某座只剩一个候选玩家(或某玩家只剩一座)→ 落座
     → 从图中消去 → 重复至不动点;
  4. 还有歧义的座【留空不猜】(纪律:宁缺毋错;留给 UNKNOWN/人工)。
"""
from __future__ import annotations


def _propagate(cands: dict, mapping: dict, used: set) -> None:
    """约束传播至不动点:座侧唯一 ∪ 玩家侧唯一,轮流收(原地改 mapping/used/cands)。"""
    changed = True
    while changed:
        changed = False
        for seat, cs in list(cands.items()):
            cs -= used
            if len(cs) == 1:
                p = next(iter(cs))
                mapping[seat] = p
                used.add(p)
                del cands[seat]
                changed = True
        owner: dict[str, list[str]] = {}
        for seat, cs in cands.items():
            for p in cs - used:
                owner.setdefault(p, []).append(seat)
        for p, seats in owner.items():
            if len(seats) == 1 and p not in used:
                seat = seats[0]
                mapping[seat] = p
                used.add(p)
                cands.pop(seat, None)
                changed = True


def map_seats(init: dict, ante_pairs: list, anchors: dict | None = None,
              tol: float = 6.0) -> dict:
    """init: {seat(str): stack|None};ante_pairs: [(player, stack|None)];
    anchors: {seat(str): player}(SB/BB/BTN 锚,可空)。返回 {seat: player}。

    ⚠️ 优先级(2026-06-12 砖3回归教训,违反即翻车):
    【值匹配+传播 在前,锚只补缺】。锚名来自 hands.seats(开手位置表),可能是
    别名变体,与事件玩家名(手内冻结/canonicalize 后)对不上;锚抢跑会把异名占座、
    挤掉正确的值匹配 → 端点挂错名 → MAPPING_GAP 反升(86%→80% 实测回归)。
    且锚名必须出现在 ante_pairs 玩家集中(同一命名空间)才许用。锚后再传播一轮。"""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    event_players = {p for p, _ in ante_pairs}

    # ⓪ 严格相等先行(2026-06-12 二次回归教训):两玩家栈值本就相近(500/504)时,
    # 精确相等各自唯一命中,±tol 容差反让两座互为候选 → 歧义双弃。
    # 分层:exact 唯一 → 直接落座;容差只兜 exact 落不了的(两次读漂移那批)。
    by_exact: dict[float, list[str]] = {}
    for p, stk in ante_pairs:
        if stk is not None:
            by_exact.setdefault(float(stk), []).append(p)
    for seat, v in init.items():
        if v is None:
            continue
        ps = [p for p in by_exact.get(float(v), []) if p not in used]
        if len(ps) == 1:
            mapping[seat] = ps[0]
            used.add(ps[0])

    # ① 剩余座:候选图(容差边)+ 传播
    cands: dict[str, set[str]] = {}
    for seat, v in init.items():
        if seat in mapping or v is None:
            continue
        cs = {p for p, stk in ante_pairs
              if p not in used and stk is not None and abs(float(v) - stk) <= tol}
        cands[seat] = cs
    _propagate(cands, mapping, used)

    # ② 锚补缺(仅未映射座 + 锚名在事件命名空间内 + 未被占用)
    for seat, player in (anchors or {}).items():
        if (seat in init and seat not in mapping
                and player in event_players and player not in used):
            mapping[seat] = player
            used.add(player)
    # ③ 锚已占座先出图,再传播一轮(锚消歧可能让剩余座变唯一)
    for seat in list(cands):
        if seat in mapping:
            del cands[seat]
    _propagate(cands, mapping, used)
    return mapping
