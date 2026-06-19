"""#226 端点否决 · 离线再处理【DRY-RUN】(2026-06,只读不改库)。

对已存的每手:走 canonical 路径 build_facts(含①假全下 ②赢家误弃 ③输家假弃牌否决)→
统计 facts.veto_log【会否决多少、各类几条】。**只报告,绝不 UPDATE/DELETE。**
真改需用户授权 + 备份(见 dev-rule-data-boundary-clarity 红线)。

用法:POKEMIR_AUDIT_DSN=postgresql://... PYTHONPATH=. .venv/bin/python \
        tools/reprocess_veto_dryrun.py --since 2026-06-12 [--hand <id前缀>]
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

from tools.solve_hands import build_facts


def fetch(dsn, since, hand_prefix=None):
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    where = "h.started_at > %s AND h.ended_at IS NOT NULL AND h.raw_data ? 'player_stacks_final'"
    params = [since]
    if hand_prefix:
        where = "h.id::text LIKE %s"
        params = [hand_prefix + "%"]
    cur.execute(
        f"""SELECT h.id::text, h.pot_size_final,
                   h.raw_data->'player_stacks_initial', h.raw_data->'player_stacks_final',
                   h.raw_data->'seat_names', h.result->'win_amounts_xx'
            FROM hands h WHERE {where} ORDER BY h.started_at""", params)
    hands = cur.fetchall()
    ids = tuple(r[0] for r in hands) or ("",)
    cur.execute(
        """SELECT ae.hand_id::text, ae.player_name, ae.street, ae.action_type, ae.amount,
                  (ae.raw_data->>'stack_before')::float
           FROM action_events ae WHERE ae.hand_id::text IN %s
           ORDER BY ae.hand_id, ae.sequence_number""", (ids,))
    acts = defaultdict(list)
    for hid, pn, st, at, amt, sb in cur.fetchall():
        acts[hid].append((pn, st, at, amt, sb))
    conn.close()
    return hands, acts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-12T00:00:00Z")
    ap.add_argument("--hand", default=None, help="只看某手(id 前缀)")
    ap.add_argument("--examples", type=int, default=10)
    args = ap.parse_args()
    dsn = os.environ.get("POKEMIR_AUDIT_DSN")
    if not dsn:
        print("需要 POKEMIR_AUDIT_DSN", file=sys.stderr); sys.exit(2)

    hands, acts = fetch(dsn, args.since, args.hand)
    n_hands = hands_touched = 0
    kind_counts = Counter()
    examples = []
    for hid, pot, psi, psf, names, xx in hands:
        if not psi or not psf:
            continue
        n_hands += 1
        h = {"id": hid, "pot": pot, "init": psi, "fin": psf, "xx": xx or {}}
        seat_map = {s: n for s, n in (names or {}).items()}
        facts = build_facts(h, acts.get(hid, []), seat_map)
        if facts.veto_log:
            hands_touched += 1
            for k, _p, _s in facts.veto_log:
                kind_counts[k] += 1
            if len(examples) < args.examples:
                examples.append((hid[:8], facts.veto_log))

    print(f"== #226 端点否决 DRY-RUN(since {args.since}{' hand='+args.hand if args.hand else ''},"
          f"{n_hands} 手,只读,canonical build_facts ①②③)==")
    print(f"  会被否决的手:  {hands_touched}/{n_hands}  ({100*hands_touched/max(n_hands,1):.1f}%)")
    for k in ("false_all_in", "winner_fold", "loser_false_fold"):
        print(f"  {k:18s}: {kind_counts.get(k, 0)}")
    print(f"\n== 样例(前 {len(examples)} 手)==")
    for hid, vl in examples:
        print(f"  {hid}: {vl}")


if __name__ == "__main__":
    main()
