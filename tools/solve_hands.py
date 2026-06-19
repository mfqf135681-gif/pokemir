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
from solver.hand_repair import (  # noqa: E402
    EXACT, REPAIRED, UNSOLVED, HandFacts, solve_hand, veto_events_by_endpoint)
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


def build_facts(h, events, seat_map, final_override=None, street_solver=True, veto=True):
    """street_solver=True(砖5):用 solve_street_contrib 时序求解每街投入(call 锁定最高注 +
    raise 栈/单调净化,治 amount 误读爆值);False=旧裸 max 聚合(对比/回退用)。
    veto=True(#226 端点否决):先算端点(net/xx/final),据其剔除假全下/赢家误弃事件,再构 facts
    (摊牌期假事件不污染 folded/contrib/守恒账;剔除清单存 facts.veto_log)。"""
    # 端点净额 + +xx 赢家 + 终栈(先算:否决要用,且不依赖 events)
    nets, xx, final_by = {}, set(), {}
    for seat, player in seat_map.items():
        i, f = h["init"].get(seat), h["fin"].get(seat)
        if player in (final_override or {}):
            f = final_override[player]   # 用下一手 initial 回填的愈合 final
        if f is not None:
            final_by[player] = float(f)
        if i is not None and f is not None:
            nets[player] = float(f) - float(i)
        if seat in (h["xx"] or {}):
            xx.add(player)
    # #226 端点否决:剔假全下/赢家误弃(端点铁证矛盾),再从干净事件流构 facts
    veto_log = []
    if veto:
        events, veto_log = veto_events_by_endpoint(events, nets, xx, final_by)
    antes, folded = {}, {}
    for player, street, atype, amount, stk_b in events:
        if atype == "post_ante" and amount:
            antes[player] = antes.get(player, 0.0) + float(amount)
        elif atype == "fold" and player not in folded:
            folded[player] = street
    if street_solver:
        from solver.street_solve import solve_street_contrib
        contrib = solve_street_contrib(events)
    else:
        contrib = {}
        for player, street, atype, amount, stk_b in events:
            if atype in ("post_sb", "post_bb") and amount is not None:
                k = (player, street); contrib[k] = max(contrib.get(k, 0.0), float(amount))
            elif atype in BRC and amount is not None:
                k = (player, street); contrib[k] = max(contrib.get(k, 0.0), float(amount))
            elif atype == "all_in":
                k = (player, street)
                if amount is not None:
                    contrib[k] = max(contrib.get(k, 0.0), float(amount))
                elif stk_b is not None:
                    contrib[k] = contrib.get(k, 0.0) + float(stk_b)
    return HandFacts(hand_id=h["id"], pot_final=h["pot"],
                     antes=antes, street_contrib=contrib, nets=nets,
                     xx_winners=xx, folded_street=folded, veto_log=veto_log)


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

    from solver.endpoint_chain import HandPoint, heal_finals

    hands, antes_ep = fetch_endpoints(dsn, args.since, args.until)
    events = fetch_events(dsn, args.since, args.until)

    # 砖4 第一遍:建跨手端点链 → 链愈合(摊牌动画拍坏的 final 用下一手 initial 回填)
    seat_maps = {h["id"]: seat_player_map(h, antes_ep) for h in hands}
    by_player = defaultdict(list)
    pots = {}
    for idx, h in enumerate(hands):
        pots[h["id"]] = h["pot"]
        for seat, player in seat_maps[h["id"]].items():
            i, f = h["init"].get(seat), h["fin"].get(seat)
            by_player[player].append(HandPoint(
                hand_id=h["id"], idx=idx, player=player,
                initial=float(i) if i is not None else None,
                final=float(f) if f is not None else None))
    _, heal_records = heal_finals(by_player, pots, tol=args.tol)
    overrides = defaultdict(dict)   # hand_id → {player: healed_final}
    for rec in heal_records:
        overrides[rec["hand"]][rec["player"]] = rec["healed_final"]

    # 第二遍:用愈合后的 final 求解
    reports, causes = [], defaultdict(list)
    for h in hands:
        sm = seat_maps[h["id"]]
        evs = events.get(h["id"], [])
        facts = build_facts(h, evs, sm, final_override=overrides.get(h["id"]))
        r = solve_hand(facts, tol=args.tol)
        reports.append(r)
        if r.status == UNSOLVED:
            # 砖2:病因分类(FINAL坏读 / J-7 pot反审 / 保险 / 映射缺口 / 小额残余)
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
