"""#226 端点否决 · 离线再处理【DRY-RUN】(2026-06,只读不改库)。

对已存的每手:从 player_stacks_initial/final 重算端点(reconstruct_hand_chips)→ 把 action_events
当逐动作候选 → 跑 veto_actions_by_endpoint → 统计【会否决多少】假全下/赢家误弃。
**只报告,绝不 UPDATE/DELETE。** 真改需用户授权 + 备份(见 dev-rule-data-boundary-clarity 红线)。

附:并列报一个"宽口径"对照——按 win_seats_xx(+xx 检测,比 net 端点更敏感)算的赢家误弃,
看 net-based 保守口径漏了多少,供决定是否扩展否决器信任 win_xx。

用法:POKEMIR_AUDIT_DSN=postgresql://... .venv/bin/python tools/reprocess_veto_dryrun.py --since 2026-06-12
"""
import argparse
import os
import sys
from collections import defaultdict

from pipeline.reconstruct import reconstruct_hand_chips, veto_actions_by_endpoint


def fetch(dsn, since):
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        """SELECT h.id::text, h.pot_size_final, h.raw_data, h.result
           FROM hands h
           WHERE h.started_at > %s AND h.ended_at IS NOT NULL
             AND h.raw_data ? 'player_stacks_final'
           ORDER BY h.started_at""", (since,))
    hands = cur.fetchall()
    cur.execute(
        """SELECT ae.hand_id::text, (ae.raw_data->>'seat_index')::int, ae.action_type
           FROM action_events ae JOIN hands h ON h.id = ae.hand_id
           WHERE h.started_at > %s AND ae.raw_data ? 'seat_index'
           ORDER BY ae.hand_id, ae.sequence_number""", (since,))
    acts = defaultdict(list)
    for hid, seat, atype in cur.fetchall():
        acts[hid].append({"seat": seat, "action_type": atype})
    conn.close()
    return hands, acts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-12T00:00:00Z")
    ap.add_argument("--examples", type=int, default=8)
    args = ap.parse_args()
    dsn = os.environ.get("POKEMIR_AUDIT_DSN")
    if not dsn:
        print("需要 POKEMIR_AUDIT_DSN", file=sys.stderr); sys.exit(2)

    hands, acts = fetch(dsn, args.since)
    n_hands = 0
    n_false_allin = n_winner_fold = 0
    hands_touched = 0
    winxx_fold_total = winxx_fold_caught = 0   # 宽口径对照
    examples = []

    for hid, pot, rd, result in hands:
        psi = rd.get("player_stacks_initial") or {}
        psf = rd.get("player_stacks_final") or {}
        if not psi or not psf:
            continue
        n_hands += 1
        initial = {int(k): float(v) for k, v in psi.items()}
        final = {int(k): float(v) for k, v in psf.items()}
        bl = rd.get("blind_level") or {}
        ep = reconstruct_hand_chips(initial, final, pot=pot,
                                    sb=bl.get("sb", 0), bb=bl.get("bb", 0), ante=bl.get("ante", 0))
        cands = acts.get(hid, [])
        res = veto_actions_by_endpoint(cands, ep, final, bb=bl.get("bb", 0))
        v_allin = [v for v in res["vetoed"] if v["action_type"] == "all_in"]
        v_fold = [v for v in res["vetoed"] if v["action_type"] == "fold"]
        n_false_allin += len(v_allin)
        n_winner_fold += len(v_fold)
        if res["vetoed"]:
            hands_touched += 1
            if len(examples) < args.examples:
                examples.append((hid, [(_v["seat"], _v["action_type"], _v["veto_reason"]) for _v in res["vetoed"]]))

        # 宽口径对照:按 win_seats_xx 的赢家误弃(net 端点没认的赢家)
        winxx = set((result or {}).get("win_seats_xx") or [])
        net_winners = set(ep["winners"])
        for a in cands:
            if a["action_type"] == "fold" and a["seat"] in winxx:
                winxx_fold_total += 1
                if a["seat"] in net_winners:
                    winxx_fold_caught += 1

    print(f"== #226 端点否决 DRY-RUN(since {args.since},{n_hands} 手,只读)==")
    print(f"  会被否决的手:        {hands_touched}/{n_hands}  ({100*hands_touched/max(n_hands,1):.1f}%)")
    print(f"  假全下 被否决:        {n_false_allin}")
    print(f"  赢家误弃 被否决(net口径): {n_winner_fold}")
    print(f"  ── 宽口径对照(win_xx)──")
    print(f"  win_xx 赢家被记弃牌:   {winxx_fold_total}")
    print(f"  其中 net 端点也认(被本否决器抓): {winxx_fold_caught}  → 漏 {winxx_fold_total - winxx_fold_caught} 由 net 口径保守放过")
    print(f"\n== 样例(前 {len(examples)} 手)==")
    for hid, vs in examples:
        print(f"  {hid}: {vs}")


if __name__ == "__main__":
    main()
