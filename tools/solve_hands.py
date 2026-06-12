"""tools/solve_hands.py — 砖1 逐手补账的 session 级验收(只读 DB)。

对每手构建 HandFacts → solve_hand → 报告:
  ① 状态分布(EXACT/REPAIRED/UNSOLVED)+ 求解后闭合率 vs 原始闭合率(audit 基线);
  ② 按底池四分位分层(KPI 必须分层读);
  ③ 修复明细样本 + UNSOLVED 登记簿行(contracts/recognition-freeze.md §5 喂料)。

用法:POKEMIR_AUDIT_DSN=... python tools/solve_hands.py --since ... [--until ...]
"""
import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from solver.diagnose import classify_unsolved  # noqa: E402
from solver.hand_repair import EXACT, REPAIRED, UNSOLVED, HandFacts, solve_hand  # noqa: E402
from tools.solve_session import fetch as fetch_endpoints, seat_player_map  # noqa: E402

BRC = ("bet", "call", "raise")


def fetch_events(dsn, since, until):
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        """SELECT ae.hand_id::text, ae.player_name, ae.street, ae.action_type, ae.amount,
                  (ae.raw_data->>'stack_before')::float
           FROM action_events ae JOIN hands h ON h.id = ae.hand_id
           WHERE h.started_at > %s AND h.started_at < %s
           ORDER BY ae.hand_id, ae.sequence_number""", (since, until))
    ev = defaultdict(list)
    for hid, player, street, atype, amount, stk_b in cur.fetchall():
        ev[hid].append((player, street, atype, amount, stk_b))
    conn.close()
    return ev


def build_facts(h, events, seat_map):
    antes, contrib, folded = {}, {}, {}
    for player, street, atype, amount, stk_b in events:
        if atype == "post_ante" and amount:
            antes[player] = antes.get(player, 0.0) + float(amount)
        elif atype in ("post_sb", "post_bb") and amount is not None:
            k = (player, street)
            contrib[k] = max(contrib.get(k, 0.0), float(amount))
        elif atype in BRC and amount is not None:
            k = (player, street)
            contrib[k] = max(contrib.get(k, 0.0), float(amount))
        elif atype == "all_in":
            k = (player, street)
            if amount is not None:
                contrib[k] = max(contrib.get(k, 0.0), float(amount))
            elif stk_b is not None:   # 旧场次 all_in 无金额 → 端点回补(同 audit 口径)
                contrib[k] = contrib.get(k, 0.0) + float(stk_b)
        elif atype == "fold" and player not in folded:
            folded[player] = street
    # 端点净额 + +xx 赢家(座→玩家映射来自值匹配)
    nets, xx = {}, set()
    for seat, player in seat_map.items():
        i, f = h["init"].get(seat), h["fin"].get(seat)
        if i is not None and f is not None:
            nets[player] = float(f) - float(i)
        if seat in (h["xx"] or {}):
            xx.add(player)
    return HandFacts(hand_id=h["id"], pot_final=h["pot"],
                     antes=antes, street_contrib=contrib, nets=nets,
                     xx_winners=xx, folded_street=folded)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="2100-01-01")
    ap.add_argument("--tol", type=float, default=6.0)
    ap.add_argument("--show-repairs", type=int, default=8)
    args = ap.parse_args()
    dsn = os.getenv("POKEMIR_AUDIT_DSN")
    if not dsn:
        sys.exit("需 POKEMIR_AUDIT_DSN(只读)")

    hands, antes_ep = fetch_endpoints(dsn, args.since, args.until)
    events = fetch_events(dsn, args.since, args.until)
    reports, pots, causes = [], {}, defaultdict(list)
    for h in hands:
        sm = seat_player_map(h, antes_ep)
        evs = events.get(h["id"], [])
        facts = build_facts(h, evs, sm)
        r = solve_hand(facts, tol=args.tol)
        reports.append(r)
        pots[h["id"]] = h["pot"]
        if r.status == UNSOLVED:
            # 砖2:病因分类(J-7 pot反审 / 保险 / 映射缺口 / 小额残余)
            d = classify_unsolved(facts, r,
                                  has_allin=any(e[2] == "all_in" for e in evs),
                                  has_insurance_hint=bool(h.get("insurance")))
            causes[d["cause"]].append((h["id"][:8], d["evidence"]))

    n = len(reports)
    by = defaultdict(list)
    for r in reports:
        by[r.status].append(r)
    raw_exact = sum(1 for r in reports if r.gap_before is not None and abs(r.gap_before) <= args.tol)
    solved = len(by[EXACT]) + len(by[REPAIRED])
    print(f"== 砖1 逐手补账:{n} 手(since {args.since}) ==")
    print(f"  原始闭合(audit 口径): {raw_exact}/{n} ({100.0*raw_exact/n:.0f}%)")
    print(f"  求解后闭合:           {solved}/{n} ({100.0*solved/n:.0f}%)"
          f"   [EXACT {len(by[EXACT])} + REPAIRED {len(by[REPAIRED])}]")
    print(f"  UNSOLVED:             {len(by[UNSOLVED])}")

    # 分层
    ps = sorted(p for p in pots.values() if p is not None)
    if ps:
        qs = [ps[int(len(ps) * q)] for q in (0.25, 0.5, 0.75)]
        print(f"\n== 按底池分层(界 {qs}) ==")
        buck = defaultdict(lambda: [0, 0])
        for r in reports:
            p = pots.get(r.hand_id)
            if p is None:
                continue
            b = sum(p > q for q in qs)
            buck[b][0] += 1
            if r.status in (EXACT, REPAIRED):
                buck[b][1] += 1
        for b in sorted(buck):
            tot, ok = buck[b]
            print(f"  Q{b+1}: {ok}/{tot} ({100.0*ok/tot:.0f}%)")

    print(f"\n== 修复样本(前 {args.show_repairs}) ==")
    shown = 0
    for r in by[REPAIRED]:
        for x in r.repairs:
            if shown >= args.show_repairs:
                break
            st = ",".join(f"{s}:{a}" for s, a in x.streets) or "-"
            print(f"  {r.hand_id[:8]} {x.kind:16s} {x.player:<14s} Δ{x.delta:<8.1f} "
                  f"街[{st}]{' ⚠️街不定' if x.street_uncertain else ''}")
            shown += 1

    print(f"\n== UNSOLVED 病因分类(砖2) ==")
    for cause, lst in sorted(causes.items(), key=lambda kv: -len(kv[1])):
        print(f"  {cause:20s} {len(lst)}")
        for hid, ev in lst[:3]:
            print(f"      {hid} {ev}")

    print(f"\n== UNSOLVED 登记簿行(喂 recognition-freeze §5) ==")
    for r in by[UNSOLVED][:10]:
        print(f"  {r.ledger_row()}")


if __name__ == "__main__":
    main()
