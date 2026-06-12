"""砖1 逐手补账 — 纯逻辑单测。场景取材实战:142 案(1eca4423)、四人all-in满分手
(ed519517,不许误补)、毒值超读、赢家侧残余、保险型补不平。"""
from solver.hand_repair import (
    AMOUNT_OVERREAD, EXACT, MISSING_CONTRIB, REPAIRED, UNSOLVED,
    HandFacts, solve_hand,
)


def facts_142():
    """1eca4423 实案简化:UTG+1 的转牌跟注 142 漏记。
    pot=3028;antes 7×4=28;实际投入:BTN 124+142+1231=1497+ante,UTG+1 124+【142漏】+1231。
    端点:UTG+1 输家 net=-(28/7? 用直接数) —— 构造:UTG+1 initial→final 净 -1501
    (ante 4 + 124 + 142 + 1231 = 1501);记录只有 1359(缺 142)。"""
    return HandFacts(
        hand_id="case142", pot_final=3028.0,
        antes={p: 4.0 for p in ["A", "B", "C", "D", "E", "F", "G"]},
        street_contrib={
            ("A", "preflop"): 124.0, ("A", "river"): 1231.0,           # A=UTG+1,转牌142漏
            ("B", "preflop"): 124.0, ("B", "turn"): 142.0, ("B", "river"): 1231.0,  # B=BTN 全
            ("C", "preflop"): 2.0, ("D", "preflop"): 4.0,              # 盲注弃
        },
        nets={"A": -1501.0, "B": 1499.0, "C": -6.0, "D": -8.0},
        xx_winners={"B"},
        folded_street={"C": "preflop", "D": "preflop"},
    )


class TestCore:
    def test_exact_hand_never_repaired(self):
        # 对照组保护:四人all-in满分手(ed519517 型)原账平 → 一根手指都不许动
        f = HandFacts(hand_id="exact", pot_final=100.0,
                      antes={"A": 4.0, "B": 4.0},
                      street_contrib={("A", "preflop"): 46.0, ("B", "preflop"): 46.0},
                      nets={"A": -50.0, "B": 42.0}, xx_winners={"B"})
        r = solve_hand(f)
        assert r.status == EXACT and r.repairs == []

    def test_case_142_repaired(self):
        r = solve_hand(facts_142())
        assert r.status == REPAIRED
        miss = [x for x in r.repairs if x.kind == MISSING_CONTRIB]
        assert len(miss) == 1 and miss[0].player == "A" and miss[0].delta == 142.0
        # 街落位:A 转牌缺口正好补到街最高注 142(C4)
        assert ("turn", 142.0) in miss[0].streets and not miss[0].street_uncertain
        assert len(miss[0].corroborators) >= 2
        assert r.gap_after == 0.0

    def test_winner_side_residual(self):
        # 输家全对,缺的是赢家自己的投入(他的下注漏记)→ C2′ 印证后补赢家侧
        f = HandFacts(hand_id="wside", pot_final=200.0,
                      antes={"A": 4.0, "B": 4.0},
                      street_contrib={("A", "flop"): 96.0, ("B", "flop"): 46.0},  # B(赢家)漏50
                      nets={"A": -100.0, "B": 90.0}, xx_winners={"B"})
        # 验算:recorded=4+4+96+46=150,gap=50;B 应投 96(跟齐),漏 50;
        # C2′:pot - net_B - recorded_B = 200-90-50=60,残余50,差10 ≤ tol6+rake30 ✓
        r = solve_hand(f)
        assert r.status == REPAIRED
        assert any(x.player == "B" and x.delta == 50.0 for x in r.repairs)

    def test_overread_marked(self):
        # 记录比端点能支持的多(毒值残余型)→ 标 AMOUNT_OVERREAD,不出修正值(v1)
        f = HandFacts(hand_id="over", pot_final=100.0,
                      antes={"A": 4.0, "B": 4.0},
                      street_contrib={("A", "preflop"): 246.0, ("B", "preflop"): 46.0},
                      nets={"A": -50.0, "B": 42.0}, xx_winners={"B"})
        r = solve_hand(f)
        assert any(x.kind == AMOUNT_OVERREAD and x.player == "A" for x in r.repairs)

    def test_unsolved_goes_to_ledger(self):
        # 缺口找不到主(无端点支持)→ UNSOLVED + 登记簿行(解冻保险条款喂料)
        f = HandFacts(hand_id="mystery-hand-001", pot_final=500.0,
                      antes={"A": 4.0}, street_contrib={("A", "preflop"): 46.0},
                      nets={}, xx_winners=set())
        r = solve_hand(f)
        assert r.status == UNSOLVED and r.ledger_row() and "mystery-" in r.ledger_row()

    def test_rake_band_tolerance(self):
        # 残余 -8(pot 比净流入少 8 = rake)→ REPAIRED 不算 UNSOLVED
        f = HandFacts(hand_id="rake", pot_final=92.0,
                      antes={"A": 4.0, "B": 4.0},
                      street_contrib={("A", "preflop"): 46.0, ("B", "preflop"): 46.0},
                      nets={"A": -50.0, "B": 34.0}, xx_winners={"B"})
        r = solve_hand(f)
        assert r.status in (EXACT, REPAIRED)


class TestStreetAssign:
    def test_multi_street_distribution(self):
        # 玩家 preflop/flop 两街都没跟满 → 缺口贪心分摊到两街
        f = HandFacts(hand_id="multi", pot_final=300.0,
                      antes={"A": 4.0, "B": 4.0},
                      street_contrib={("A", "preflop"): 50.0, ("A", "flop"): 100.0,
                                      ("B", "preflop"): 20.0, ("B", "flop"): 0.0},
                      nets={"B": -154.0, "A": 134.0}, xx_winners={"A"})
        r = solve_hand(f)
        m = next(x for x in r.repairs if x.player == "B")
        assert dict(m.streets) == {"preflop": 30.0, "flop": 100.0}
        assert not m.street_uncertain

    def test_uncertain_when_no_street_max(self):
        # 缺口大于一切街可解释量(他自己是最高注者)→ 落最后在场街 + 标不确定
        f = HandFacts(hand_id="unc", pot_final=300.0,
                      antes={"A": 4.0, "B": 4.0},
                      street_contrib={("A", "flop"): 100.0, ("B", "flop"): 100.0},
                      nets={"B": -196.0, "A": 188.0}, xx_winners={"A"})
        r = solve_hand(f)
        m = next(x for x in r.repairs if x.player == "B")
        assert m.street_uncertain and m.delta == 92.0
