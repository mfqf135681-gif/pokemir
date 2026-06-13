"""tools/compare_sessions.py — 新旧 session 求解质量对比(只读)。

用途:量化一次识别层修复(如 dedup 解冻修)的实效——同一套求解器跑【修前】
与【修后】两个时间窗,对比 hand-closure(总+分层)、dedup 误杀量、病因谱。

dedup 实效专项:修后 dedup_skip 的 payload 带 amount/last_amount(2026-06-12 起),
"金额显著不同却被 skip"= 残余误杀;修前无金额字段,只能看总 skip 数(粗)。

用法:
  POKEMIR_AUDIT_DSN=... python tools/compare_sessions.py \
      --old-since ... --old-until ... --new-since ... [--new-until ...]
"""
import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from solver.diagnose import classify_unsolved  # noqa: E402
from solver.endpoint_chain import HandPoint, heal_finals  # noqa: E402
from solver.hand_repair import EXACT, REPAIRED, UNSOLVED, solve_hand  # noqa: E402
from tools.solve_hands import build_facts, fetch_events  # noqa: E402
from tools.solve_session import fetch as fetch_endpoints, seat_player_map  # noqa: E402


def _solve_window(dsn, since, until):
    hands, antes = fetch_endpoints(dsn, since, until)
    events = fetch_events(dsn, since, until)
    seat_maps = {h["id"]: seat_player_map(h, antes) for h in hands}
    by_player = defaultdict(list)
    pots = {}
    for idx, h in enumerate(hands):
        pots[h["id"]] = h["pot"]
        for seat, pl in seat_maps[h["id"]].items():
            i, f = h["init"].get(seat), h["fin"].get(seat)
            by_player[pl].append(HandPoint(hand_id=h["id"], idx=idx, player=pl,
                                           initial=float(i) if i is not None else None,
                                           final=float(f) if f is not None else None))
    _, recs = heal_finals(by_player, pots)
    ov = defaultdict(dict)
    for r in recs:
        ov[r["hand"]][r["player"]] = r["healed_final"]

    rows = []
    causes = defaultdict(int)
    for h in hands:
        evs = events.get(h["id"], [])   # fetch_events 已返 (player,street,action,amount,stk_b) 5元组
        facts = build_facts(h, evs, seat_maps[h["id"]], final_override=ov.get(h["id"]))
        rep = solve_hand(facts)
        if rep.status == UNSOLVED:
            d = classify_unsolved(facts, rep,
                                  has_allin=any(t[2] == "all_in" for t in evs),
                                  has_insurance_hint=bool(h.get("insurance")))
            causes[d["cause"]] += 1
        rows.append((h["id"], h["pot"], rep.status))
    return rows, causes


def _dedup_misfires(dsn, since, until):
    """修后(payload 带金额)统计 dedup 误杀:金额显著不同却被 skip = 误杀残余。"""
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        """SELECT de.payload->>'amount', de.payload->>'last_amount'
           FROM diagnostic_events de JOIN hands h ON h.id=de.hand_id
           WHERE de.tag='action.dedup_skip' AND h.started_at > %s AND h.started_at < %s""",
        (since, until))
    total = misfire = no_amt = 0
    for a, la in cur.fetchall():
        total += 1
        if a is None or la is None:
            no_amt += 1   # 修前无金额字段 / check-fold
        elif abs(float(a) - float(la)) > 2.0:
            misfire += 1  # 金额显著不同却被 skip = 误杀(修后应≈0)
    conn.close()
    return {"total": total, "misfire": misfire, "no_amount_field": no_amt}


def _closure(rows, tol_pot_quartiles=None):
    n = len(rows)
    solved = sum(1 for _, _, s in rows if s in (EXACT, REPAIRED))
    pots = sorted(p for _, p, _ in rows if p is not None)
    layers = {}
    if pots:
        qs = [pots[int(len(pots) * q)] for q in (0.25, 0.5, 0.75)]
        buck = defaultdict(lambda: [0, 0])
        for _, p, s in rows:
            if p is None:
                continue
            b = sum(p > q for q in qs)
            buck[b][0] += 1
            if s in (EXACT, REPAIRED):
                buck[b][1] += 1
        layers = {f"Q{b+1}": (ok, tot) for b, (tot, ok) in
                  ((b, buck[b]) for b in sorted(buck))}
    return n, solved, layers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-since", required=True)
    ap.add_argument("--old-until", required=True)
    ap.add_argument("--new-since", required=True)
    ap.add_argument("--new-until", default="2100-01-01")
    args = ap.parse_args()
    dsn = os.getenv("POKEMIR_AUDIT_DSN")
    if not dsn:
        sys.exit("需 POKEMIR_AUDIT_DSN(只读)")

    for tag, since, until in (("修前(OLD)", args.old_since, args.old_until),
                              ("修后(NEW)", args.new_since, args.new_until)):
        rows, causes = _solve_window(dsn, since, until)
        n, solved, layers = _closure(rows)
        dd = _dedup_misfires(dsn, since, until)
        print(f"\n===== {tag}  ({since} → {until}) =====")
        print(f"  hand-closure: {solved}/{n} ({100.0*solved/n:.0f}%)" if n else "  无手")
        if layers:
            print("  分层: " + "  ".join(
                f"{q}:{ok}/{tot}({100*ok//tot if tot else 0}%)" for q, (ok, tot) in layers.items()))
        print(f"  dedup_skip: 总{dd['total']} / 误杀(金额显著不同){dd['misfire']} "
              f"/ 无金额字段{dd['no_amount_field']}")
        if causes:
            print("  补不平病因: " + "  ".join(f"{c}:{n_}" for c, n_ in
                                          sorted(causes.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
