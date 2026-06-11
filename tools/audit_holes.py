"""漏抓验尸分类器(只读)— 把"后街有动作、本街无动作"的铁证洞按病因分类。

洞 = 玩家在某后续街有动作但街 N 没有(他必然在街 N 行动过却无记录)。病因:
  ID_DRIFT       同手同位置存在另一个名字在缺失街有动作 → 动作抓到了记错人(不伤守恒,毒画像/归属)
  STREET_SWALLOW 缺失街全桌零事件 → 整街隐身;若该手守恒 EXACT = 全过牌街(丢动作不丢钱),
                 机制吻合 diff-cache:overlay 挂着不消失,连续同动作像素不变被跳过
  GENUINE_MISS   其余 = 真·捕获漏;附"收街位?"(在该街已记录玩家之后行动 → 短窗假说)

另:全场 ID 漂移量化(同手同位置 ≥2 个名字有主动动作的手数)。

用法:POKEMIR_AUDIT_DSN=... python tools/audit_holes.py --since 2026-06-11T10:04:00Z
"""
import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.audit_session import fetch, hand_conservation  # noqa: E402

ACT = ("check", "call", "bet", "raise", "fold", "all_in")
STREETS = ["preflop", "flop", "turn", "river"]
PRE = ["UTG", "UTG+1", "MP", "HJ", "CO", "BTN", "SB", "BB"]   # 翻前行动序
POST = ["SB", "BB", "UTG", "UTG+1", "MP", "HJ", "CO", "BTN"]  # 翻后行动序


def order_idx(street, pos):
    seq = PRE if street == "preflop" else POST
    return seq.index(pos) if pos in seq else -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-11T10:04:00Z")
    args = ap.parse_args()
    dsn = os.getenv("POKEMIR_AUDIT_DSN")
    if not dsn:
        sys.exit("需 POKEMIR_AUDIT_DSN(只读)")

    hands, ev = fetch(dsn, args.since)
    holes = []
    drift_hands = []

    for h in hands:
        events = ev[h["id"]]
        by_ps = defaultdict(set)          # player → 有主动动作的街集合
        st_evs = defaultdict(list)        # street → 主动事件列表
        pos_of = {}                       # player → position
        for e in events:
            if e.get("pos") and e["player"] not in pos_of:
                pos_of[e["player"]] = e["pos"]
            if e["action"] in ACT:
                by_ps[e["player"]].add(e["street"])
                st_evs[e["street"]].append(e)
        cons, _, _ = hand_conservation(events, h["pot"])

        # 全场 ID 漂移:同位置多名字有主动动作
        names_at = defaultdict(set)
        for e in events:
            if e["action"] in ACT and e.get("pos"):
                names_at[e["pos"]].add(e["player"])
        multi = {p: ns for p, ns in names_at.items() if len(ns) > 1}
        if multi:
            drift_hands.append((h["id"][:8], multi))

        for player, streets in by_ps.items():
            for i, s in enumerate(STREETS[:-1]):
                later = [t for t in STREETS[i + 1:] if t in streets]
                if s in streets or not later:
                    continue
                if not st_evs[s]:
                    cls = "STREET_SWALLOW" + ("(全过牌街,不丢钱)" if cons == "EXACT" else "(可能丢钱)")
                elif pos_of.get(player) and any(
                        e["player"] != player and e.get("pos") == pos_of[player]
                        for e in st_evs[s]):
                    cls = "ID_DRIFT"
                else:
                    my = order_idx(s, pos_of.get(player, ""))
                    others = [order_idx(s, e.get("pos") or "") for e in st_evs[s]]
                    closing = my >= 0 and others and my > max(others)
                    cls = "GENUINE_MISS" + ("(收街位)" if closing else "(非收街位)")
                holes.append({"hand": h["id"][:8], "player": player, "street": s,
                              "class": cls, "cons": cons})

    print(f"== 洞分类({len(holes)} 个 / {len(hands)} 手) ==")
    by_cls = defaultdict(list)
    for x in holes:
        by_cls[x["class"]].append(x)
    for cls, lst in sorted(by_cls.items(), key=lambda kv: -len(kv[1])):
        print(f"  {cls:36s} {len(lst)}")
    print("\n== 明细 ==")
    for x in sorted(holes, key=lambda x: (x["class"], x["hand"])):
        print(f"  {x['hand']} {x['street']:8s} {x['class']:36s} 守恒={x['cons']:8s} {x['player']}")

    print(f"\n== ID 漂移(同手同位置多名字,{len(drift_hands)} 手) ==")
    for hid, multi in drift_hands[:20]:
        det = "; ".join(f"{p}:{'/'.join(sorted(ns))}" for p, ns in multi.items())
        print(f"  {hid} {det}")
    if len(drift_hands) > 20:
        print(f"  …共 {len(drift_hands)} 手")


if __name__ == "__main__":
    main()
