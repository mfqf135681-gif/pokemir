"""逐手求解复盘时间线 — 纯逻辑单测(推断/记录区分、UNSOLVED 病因透出)。"""
from solver.hand_repair import HandFacts, solve_hand
from solver.replay_view import build_solved_timeline


def _rows():
    return [
        {"seq": 1, "street": "preflop", "player": "A", "action": "raise", "amount": 124.0},
        {"seq": 2, "street": "preflop", "player": "B", "action": "call", "amount": 124.0},
        {"seq": 3, "street": "turn", "player": "B", "action": "bet", "amount": 142.0},
    ]


def test_repair_rows_visually_distinct():
    f = HandFacts(hand_id="h", pot_final=532.0,
                  antes={"A": 4.0, "B": 4.0},
                  street_contrib={("A", "preflop"): 124.0, ("B", "preflop"): 124.0,
                                  ("B", "turn"): 142.0},
                  nets={"A": -270.0, "B": 254.0}, xx_winners={"B"})
    r = solve_hand(f)   # A 漏 turn 跟注 142 → REPAIRED
    tl = build_solved_timeline(f, r, _rows())
    assert tl["status"] == "REPAIRED" and tl["repairs_n"] == 1
    turn_rows = next(s["rows"] for s in tl["streets"] if s["street"] == "turn")
    inferred = [x for x in turn_rows if x["source"] == "🔧推断"]
    recorded = [x for x in turn_rows if x["source"] == "记录"]
    assert len(inferred) == 1 and inferred[0]["player"] == "A" and "印证" in inferred[0]["note"]
    assert len(recorded) == 1   # 原始行原样保留


def test_unsolved_exposes_cause():
    f = HandFacts(hand_id="u", pot_final=500.0, antes={"A": 4.0},
                  street_contrib={("A", "preflop"): 46.0}, nets={}, xx_winners=set())
    r = solve_hand(f)
    tl = build_solved_timeline(f, r, [], cause={"cause": "MAPPING_GAP", "evidence": "x"})
    assert tl["status"] == "UNSOLVED" and tl["cause"] == "MAPPING_GAP"
    assert tl["residual"] is not None   # 不假装完整


def test_exact_no_inferred_rows():
    f = HandFacts(hand_id="e", pot_final=256.0, antes={"A": 4.0, "B": 4.0},
                  street_contrib={("A", "preflop"): 124.0, ("B", "preflop"): 124.0},
                  nets={"A": -128.0, "B": 120.0}, xx_winners={"B"})
    r = solve_hand(f)
    tl = build_solved_timeline(f, r, _rows()[:2])
    assert tl["status"] == "EXACT"
    assert all(x["source"] == "记录" for s in tl["streets"] for x in s["rows"])
