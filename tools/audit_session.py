"""全量 session 审计(只读)— 信源验证的"真验收"层。

逐手守恒核算:用德州显示语义重算每手总投入,对账 pot_size_final。
  - 金额是【本街累计】:每(玩家,街)取 bet/call/raise 的 max(amount)
  - 翻前地板 = 已派盲注(弃牌的 SB/BB 也贡献盲注)
  - all_in 无金额 → 用 stack_before 回补(街累计 = 此前累计 + 全推栈)
  - ante 不进座位显示,单独累加
分类:EXACT(|gap|≤tol)/ GAP_POS(pot>记录=漏抓)/ GAP_NEG(记录>pot=超读)/ UNKNOWN
赢家归属:端点法 — 玩家下一手 ante 时刻栈 − 本手 ante 时刻栈 = 本手净得,
  赢家净得应 ≈ pot − 本人投入(误差≤tol;next 栈跳升>pot 标 rebuy 嫌疑跳过)。

用法:
  POKEMIR_AUDIT_DSN=postgresql://... python tools/audit_session.py --since 2026-06-11T10:04:00Z
"""
import argparse
import json
import os
import sys
from collections import defaultdict

BRC = ("bet", "call", "raise")


def fetch(dsn, since):
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        """SELECT h.id::text, h.started_at, h.pot_size_final
           FROM hands h WHERE h.started_at > %s AND h.ended_at IS NOT NULL
           ORDER BY h.started_at""", (since,))
    hands = [{"id": r[0], "started_at": r[1], "pot": r[2]} for r in cur.fetchall()]
    cur.execute(
        """SELECT ae.hand_id::text, ae.sequence_number, ae.street, ae.player_name,
                  ae.action_type, ae.amount, (ae.raw_data->>'stack_before')::float,
                  ae.position
           FROM action_events ae JOIN hands h ON h.id = ae.hand_id
           WHERE h.started_at > %s ORDER BY ae.hand_id, ae.sequence_number""", (since,))
    ev = defaultdict(list)
    for hid, seq, street, player, atype, amount, stk_b, pos in cur.fetchall():
        ev[hid].append({"seq": seq, "street": street, "player": player,
                        "action": atype, "amount": amount, "stk_b": stk_b,
                        "pos": pos})
    conn.close()
    return hands, ev


def hand_conservation(events, pot, tol=2.0):
    """返回 (status, gap, detail)。gap = pot − 重算总投入(正=漏抓,负=超读)。"""
    antes = sum(e["amount"] or 0 for e in events if e["action"] == "post_ante")
    contrib = defaultdict(float)   # (player, street) → 街累计
    unknown = []
    for e in events:
        key = (e["player"], e["street"])
        a, amt = e["action"], e["amount"]
        if a in ("post_sb", "post_bb") and amt:
            contrib[key] = max(contrib[key], amt)
        elif a in BRC and amt is not None:
            contrib[key] = max(contrib[key], amt)
        elif a == "all_in":
            if e["stk_b"] is not None:
                contrib[key] = contrib[key] + e["stk_b"]
            else:
                unknown.append(e)
    if pot is None:
        return "UNKNOWN", None, "pot_size_final 缺"
    if unknown:
        return "UNKNOWN", None, f"all_in 无 stack_before ×{len(unknown)}"
    total = antes + sum(contrib.values())
    gap = pot - total
    if abs(gap) <= tol:
        return "EXACT", gap, ""
    return ("GAP_POS" if gap > 0 else "GAP_NEG"), gap, f"pot={pot} 重算={total}"


def winner_check(hands, ev, tol=6.0):
    """端点法赢家归属:净得≈pot−投入。返回 (可判手数, 通过数, 嫌疑列表)。"""
    # 每玩家的 (hand_idx, ante 时刻栈)
    stk = defaultdict(list)
    for i, h in enumerate(hands):
        for e in ev[h["id"]]:
            if e["action"] == "post_ante" and e["stk_b"] is not None:
                stk[e["player"]].append((i, e["stk_b"]))
    nxt = {}  # (player, hand_idx) → 下一手 ante 栈
    for p, lst in stk.items():
        for (i, s), (j, s2) in zip(lst, lst[1:]):
            if j == i + 1:           # 只取紧邻下一手,隔手易混入他手输赢
                nxt[(p, i)] = s2
    judged = passed = 0
    fails = []
    for i, h in enumerate(hands):
        if h["pot"] is None:
            continue
        # 本手每玩家投入(含盲注/ante)
        inv = defaultdict(float)
        cs = defaultdict(float)
        for e in ev[h["id"]]:
            if e["action"] == "post_ante" and e["amount"]:
                inv[e["player"]] += e["amount"]
            k = (e["player"], e["street"])
            if e["action"] in ("post_sb", "post_bb", *BRC) and e["amount"] is not None:
                cs[k] = max(cs[k], e["amount"])
            elif e["action"] == "all_in" and e["stk_b"] is not None:
                cs[k] += e["stk_b"]
        for (p, _s), v in cs.items():
            inv[p] += v
        # 未跟注退还(2026-06-11):某街最高投入若无人跟满(max>第二高),差额结算时退回
        # 最高者(短all-in/全场弃牌场景;被跟满的街 max==第二高 → 退还0)。pot显示含退还
        # 前金额 → 赢家期望净得需扣"退给别人"的部分;退还归赢家自己时 pot−inv 不变。
        # ⚠️ 只在该手守恒 EXACT(记录完整)时启用:raw 数据漏抓的跟注会让最高投入【假装】
        # 没被跟满 → 虚算退还(首跑不带此条件:归属 61%→59% 反噬 + 出现负期望)。
        refund, refund_owner = 0.0, None
        cons_status, _, _ = hand_conservation(ev[h["id"]], h["pot"])
        if cons_status == "EXACT":
            by_street = defaultdict(list)
            for (p, s), v in cs.items():
                by_street[s].append((v, p))
            for s, lst in by_street.items():
                lst.sort(reverse=True)
                if len(lst) >= 2 and lst[0][0] > lst[1][0]:
                    r = lst[0][0] - lst[1][0]
                    if r > refund:       # 实际只可能发生在最后下注街,取最大者即它
                        refund, refund_owner = r, lst[0][1]
        # 净得:有紧邻下一手栈的玩家
        nets = {}
        for e in ev[h["id"]]:
            if e["action"] != "post_ante" or e["stk_b"] is None:
                continue
            p = e["player"]
            if (p, i) in nxt:
                nets[p] = nxt[(p, i)] - e["stk_b"]
        if not nets:
            continue
        w = max(nets, key=nets.get)
        if nets[w] <= 0:
            continue                       # 赢家不在可判集合(下一手没坐/栈读缺)
        if nets[w] > h["pot"] + tol:
            continue                       # 净得>底池 = rebuy 嫌疑,不判
        judged += 1
        expect = h["pot"] - inv.get(w, 0.0)
        if refund_owner is not None and refund_owner != w:
            expect -= refund             # 退还给非赢家 → 赢家实拿 = pot − 退还
        if abs(nets[w] - expect) <= max(tol, 0.05 * h["pot"]):
            passed += 1
        else:
            fails.append((h["id"][:8], w, round(nets[w], 1), round(expect, 1), h["pot"]))
    return judged, passed, fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-11T10:04:00Z")
    ap.add_argument("--tol", type=float, default=2.0)
    ap.add_argument("--dump", help="逐手结果写 JSONL 到此路径")
    args = ap.parse_args()
    dsn = os.getenv("POKEMIR_AUDIT_DSN")
    if not dsn:
        sys.exit("需 POKEMIR_AUDIT_DSN 环境变量(只读审计,不写库)")

    hands, ev = fetch(dsn, args.since)
    rows, by_status = [], defaultdict(list)
    for h in hands:
        status, gap, detail = hand_conservation(ev[h["id"]], h["pot"], args.tol)
        rows.append({"hand": h["id"], "pot": h["pot"], "status": status,
                     "gap": gap, "detail": detail})
        by_status[status].append((h, gap))

    n = len(hands)
    print(f"== 守恒审计:{n} 手(since {args.since},tol ±{args.tol}) ==")
    for st in ("EXACT", "GAP_POS", "GAP_NEG", "UNKNOWN"):
        lst = by_status.get(st, [])
        print(f"  {st:8s} {len(lst):4d}  ({100.0*len(lst)/n:.1f}%)")

    # 按底池四分位分层
    pots = sorted(h["pot"] for h in hands if h["pot"] is not None)
    if pots:
        qs = [pots[int(len(pots) * q)] for q in (0.25, 0.5, 0.75)]
        print(f"\n== 按底池分层(四分位界 {qs}) ==")
        buckets = defaultdict(lambda: [0, 0])
        for r in rows:
            if r["pot"] is None:
                continue
            b = sum(r["pot"] > q for q in qs)
            buckets[b][0] += 1
            if r["status"] == "EXACT":
                buckets[b][1] += 1
        for b in sorted(buckets):
            tot, ok = buckets[b]
            lo = "≤" + str(qs[0]) if b == 0 else f">{qs[b-1]}"
            print(f"  Q{b+1}({lo:>6s}): {ok}/{tot} EXACT ({100.0*ok/tot:.0f}%)")

    # 最差 10 手
    worst = sorted((r for r in rows if r["gap"] is not None and abs(r["gap"]) > args.tol),
                   key=lambda r: -abs(r["gap"]))[:10]
    if worst:
        print("\n== 最差 10 手 ==")
        for r in worst:
            print(f"  {r['hand'][:8]} pot={r['pot']:>6} gap={r['gap']:>+8.1f}  {r['detail']}")

    judged, passed, fails = winner_check(hands, ev)
    print(f"\n== 赢家归属(端点法,紧邻下手栈可判) ==")
    print(f"  可判 {judged} 手,通过 {passed}({100.0*passed/judged:.0f}%)" if judged else "  无可判手")
    for f in fails[:10]:
        print(f"  FAIL {f[0]} 赢家={f[1]} 净得={f[2]} 期望={f[3]} pot={f[4]}")

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fp:
            for r in rows:
                fp.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        print(f"\n逐手结果 → {args.dump}")


if __name__ == "__main__":
    main()
