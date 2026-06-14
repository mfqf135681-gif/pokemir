"""tools/solve_session.py — 砖0 端点链清洗的 session 级报告(只读 DB)。

产出四张表:
  ① 接缝分类统计(REBUY / SUSPECT_READ 明细)— #241 落地形态;
  ② per-hand 残差 + rake 基线(D25);
  ③ 端点法归属 vs +xx 双信源一致率(对照 audit_session 旧法 59% 基线);
  ④ "won_exceeds_pot" 等异常 flag 清单(喂砖1)。

座位↔玩家映射:post_ante 的 stack_before 与 raw_data.player_stacks_initial[seat]
来自同一次采集 → 值精确匹配(撞值时标 unknown 不猜)。

用法:POKEMIR_AUDIT_DSN=... python tools/solve_session.py --since 2026-06-12T05:30:00Z
"""
import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from solver.endpoint_chain import (  # noqa: E402
    REBUY, SUSPECT_READ, HandPoint, attribute_winners, classify_seams,
    hand_residuals, pair_outliers, rake_baseline,
)


def fetch(dsn, since, until):
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        """SELECT h.id::text, h.pot_size_final,
                  h.raw_data->'player_stacks_initial', h.raw_data->'player_stacks_final',
                  h.result->'win_amounts_xx',
                  h.seats, h.raw_data->>'sb_seat', h.raw_data->>'bb_seat',
                  h.raw_data->>'button_seat_index', h.raw_data->'insurance_inferred',
                  h.raw_data->'seat_names'
           FROM hands h WHERE h.started_at > %s AND h.started_at < %s AND h.ended_at IS NOT NULL
           ORDER BY h.started_at""", (since, until))
    hands = [{"id": r[0], "pot": r[1], "init": r[2] or {}, "fin": r[3] or {},
              "xx": r[4] or {}, "seats": r[5] or {}, "sb_seat": r[6], "bb_seat": r[7],
              "btn_seat": r[8], "insurance": r[9] or [], "seat_names": r[10] or {}}
             for r in cur.fetchall()]
    cur.execute(
        """SELECT ae.hand_id::text, ae.player_name, (ae.raw_data->>'stack_before')::float
           FROM action_events ae JOIN hands h ON h.id = ae.hand_id
           WHERE h.started_at > %s AND h.started_at < %s AND ae.action_type = 'post_ante'""",
        (since, until))
    antes = defaultdict(list)
    for hid, player, stk in cur.fetchall():
        antes[hid].append((player, stk))
    conn.close()
    return hands, antes


def seat_player_map(hand, antes):
    """seat(str) → player。
    ⭐2026-06-13 座位主键根治:live 落库 hands.raw_data.seat_names = {seat: name}
    (player_id_map 快照)→ 直接返回,零反推,MAPPING_GAP 整类消失。
    历史数据(无 seat_names)回退砖3 容差匹配+锚(map_seats),向后兼容。"""
    seat_names = hand.get("seat_names")
    if seat_names:
        return {str(s): p for s, p in seat_names.items() if p}
    # —— 以下为历史数据 fallback(无 seat_names 的旧手):砖3 值匹配+传播+锚 ——
    from solver.mapping import map_seats
    seats_by_pos = hand.get("seats") or {}
    anchors = {}
    for seat_key, pos in ((hand.get("sb_seat"), "SB"), (hand.get("bb_seat"), "BB"),
                          (hand.get("btn_seat"), "BTN")):
        player = seats_by_pos.get(pos)
        if seat_key is not None and player:
            anchors[str(seat_key)] = player
    init = {s: (float(v) if v is not None else None) for s, v in hand["init"].items()}
    return map_seats(init, antes.get(hand["id"], []), anchors=anchors)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="2100-01-01",
                    help="上界(不含);不给会把后续 session 粘进来,跨场接缝失真")
    ap.add_argument("--tol", type=float, default=6.0)
    args = ap.parse_args()
    dsn = os.getenv("POKEMIR_AUDIT_DSN")
    if not dsn:
        sys.exit("需 POKEMIR_AUDIT_DSN(只读)")

    hands, antes = fetch(dsn, args.since, args.until)
    by_player = defaultdict(list)       # player → [HandPoint]
    by_hand = {}                        # hand_id → [HandPoint]
    unmapped = 0
    for idx, h in enumerate(hands):
        m = seat_player_map(h, antes)
        unmapped += len(h["init"]) - len(m)
        pts = []
        for seat, player in m.items():
            fin = h["fin"].get(seat)
            xx = h["xx"].get(seat)
            p = HandPoint(hand_id=h["id"], idx=idx, player=player,
                          initial=float(h["init"][seat]) if h["init"].get(seat) is not None else None,
                          final=float(fin) if fin is not None else None,
                          win_xx=float(xx) if xx is not None else None)
            pts.append(p)
            by_player[player].append(p)
        by_hand[h["id"]] = pts

    print(f"== 砖0 端点链清洗:{len(hands)} 手(since {args.since});座位映射缺口 {unmapped} 座次 ==")

    # ① 接缝(+ 链级单点离群:反号互抵对 = 中间手端点读偏,非真补码)
    seams, outliers = [], []
    for player, chain in by_player.items():
        ss = classify_seams(sorted(chain, key=lambda x: x.idx), tol=args.tol)
        seams += ss
        outliers += pair_outliers(ss)
    outlier_hands = {(o["player"], o["hand"]) for o in outliers}
    kinds = defaultdict(int)
    for s in seams:
        kinds[s.kind] += 1
    print(f"\n== ① 接缝分类({len(seams)} 条;链级离群手 {len(outliers)}) ==")
    for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {k:14s} {n}")
    for o in outliers:
        print(f"  OUTLIER_HAND {o['player']:<14s} {o['hand'][:8]} "
              f"(入{o['gap_in']:+.0f}/出{o['gap_out']:+.0f})")
    for s in seams:
        if s.kind in (REBUY, SUSPECT_READ) and \
                (s.player, s.next_hand) not in outlier_hands and \
                (s.player, s.prev_hand) not in outlier_hands:
            print(f"  {s.kind:12s} {s.player:<14s} gap={s.gap:+8.1f}"
                  f"{'  [整数]' if s.round_hint else ''}  {s.prev_hand[:8]}→{s.next_hand[:8]}")

    # ② 残差 + rake
    res = hand_residuals(by_hand, tol_seat=args.tol)
    bl = rake_baseline(res)
    n_cand = sum(1 for r in res.values() if r["rake_candidate"])
    print(f"\n== ② rake 基线(候选手 {n_cand}/{len(res)}) ==")
    print(f"  {bl}" if bl else "  样本不足(<20),冷启动期维持保守容忍")

    # ③ 归属一致率
    judged = agree = 0
    flags_all = []
    for h in hands:
        pts = by_hand[h["id"]]
        if not pts:
            continue
        r = attribute_winners(pts, h["pot"], tol=args.tol)
        if r["agree_xx"] is not None:
            judged += 1
            agree += int(r["agree_xx"])
        for f in r["flags"]:
            flags_all.append((h["id"][:8], f))
    print(f"\n== ③ 端点×+xx 归属一致率:{agree}/{judged}"
          f"({100.0*agree/judged:.0f}%)" if judged else "\n== ③ 无可判手 ==")

    print(f"\n== ④ 异常 flag({len(flags_all)}) ==")
    for hid, f in flags_all[:15]:
        print(f"  {hid} {f}")


if __name__ == "__main__":
    main()
