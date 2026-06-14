"""solver/street_solve.py — 砖5:每街每人投入(to-amount)时序求解。

替代"裸 max 聚合"(会被 amount 误读爆值污染:铁臂 call 读爆成 644798 → 街投入假装
644798 → RECORD_OVERREAD)。用德州规则+UI物理两条独立洞察(用户 2026-06-13):

  ① 先行动者金额稳定呈现 → raise/bet/盲注的读数可信(但仍过栈+单调上界,防个别误读);
  ② 当前焦点/最后行动者动画期误读 → 不信其 amount 读数:
     - call 的 to-amount = 当前街最高注(前位稳定值锁定,不读 call 自己那帧);
     - 封闭街的最后行动者只能 call/check(raise 不封闭街)→ 同样被最高注/0 锁死。

核心:to-amount 受德州硬约束 —— 单调不减、≤自己的栈、call=当前最高注。
按时序逐动作维护 current_max_bet,call 锁定到它,raise 须 >max 且 ≤ 栈才更新(否则
视误读、该座至少跟到 max)。盲注/ante 稳定直接计。

纯逻辑,无 DB/cv2,Linux 全可测。圈梁纪律:只用真值读数+规则,深度=1,不改源数据。
"""
from __future__ import annotations

from collections import defaultdict

POST = ("post_sb", "post_bb", "post_ante")


def solve_street_contrib(events, tol: float = 2.0) -> dict:
    """events: [(player, street, action, amount, stk_b)] 时序(按 sequence)。
    返回 {(player, street): to_amount}。

    amount/stk_b 可为 None(读空)。栈上界用该动作帧的 stk_b(行动前栈)——raise/call
    的投入不可能超过行动前的栈。"""
    by_street: dict[str, list] = defaultdict(list)
    for e in events:
        by_street[e[1]].append(e)

    out: dict[tuple, float] = {}
    for street, evs in by_street.items():
        cur_max = 0.0                      # 当前街最高注(到额口径)
        contrib: dict[str, float] = {}     # player → 本街已确认 to-amount
        for (player, _s, action, amount, stk_b) in evs:
            cap = (stk_b + tol) if stk_b is not None else None   # 该座行动前栈上界
            if action == "post_ante":
                continue                   # ante 由 build_facts 单列(不进 street_contrib 口径)
            elif action in ("post_sb", "post_bb"):
                if amount is not None:
                    contrib[player] = max(contrib.get(player, 0.0), amount)
                    cur_max = max(cur_max, amount)               # 大盲设初始最高注
            elif action in ("bet", "raise"):
                # 先行动者稳定:读数 > 当前最高注 且 ≤ 栈 → 可信,更新最高注;
                # 否则(误读:不增 / 爆栈)→ 忽略读数,但他至少跟到了 max(下注/加注必≥跟注)
                if amount is not None and amount > cur_max and (cap is None or amount <= cap):
                    cur_max = amount
                    contrib[player] = amount
                else:
                    contrib[player] = max(contrib.get(player, 0.0), cur_max)
            elif action == "call":
                # 焦点/最后行动者动画期误读 → 不信 call 那帧金额,锁定到当前最高注(前位稳定)。
                # 栈上界(2026-06-14 用户揪出盲区):短码玩家面对 > 自己栈的注,"跟"=全推到
                # 自己栈(短码 call = all-in),跟不满最高注。多人 all-in 局 cur_max 被抬高 →
                # 这种短码跟最常见、高估最严重。到额 = min(最高注, 已投+行动前栈);stk_b 缺则
                # 退回 cur_max(无栈信息,假设跟满)。注:不解【最大全推未跟满的退还】(归退还规则)。
                target = cur_max
                if stk_b is not None:
                    target = min(cur_max, contrib.get(player, 0.0) + stk_b)
                contrib[player] = max(contrib.get(player, 0.0), target)
            elif action == "all_in":
                # 全推:投入 = 行动前栈(stk_b);可能 > cur_max(over-shove)或 ≤(短码)
                if stk_b is not None:
                    v = contrib.get(player, 0.0) + stk_b
                    contrib[player] = v
                    cur_max = max(cur_max, v)
                elif amount is not None and (cap is None or amount <= cap):
                    contrib[player] = max(contrib.get(player, 0.0), amount)
                    cur_max = max(cur_max, contrib[player])
            # check/fold:不投入,不动 contrib
        for p, v in contrib.items():
            out[(p, street)] = round(v, 1)
    return out
