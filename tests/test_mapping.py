"""砖3 整环座位映射 — 纯逻辑单测。"""
from solver.mapping import map_seats


class TestMapSeats:
    def test_tolerance_match(self):
        # 两次读差 4(实测噪声)→ 容差 6 内照样落座;严格相等的旧法会漏
        m = map_seats({"0": 500.0, "1": 300.0},
                      [("A", 496.0), ("B", 300.0)])
        assert m == {"0": "A", "1": "B"}

    def test_ambiguous_left_unmapped(self):
        # 两人同栈(撞值)且无锚 → 宁缺毋错,两座都留空
        m = map_seats({"0": 500.0, "1": 500.0},
                      [("A", 500.0), ("B", 500.0)])
        assert m == {}

    def test_anchor_breaks_tie(self):
        # 同栈撞值但 seat 0 是 SB 锚 → 锚定 A 后,B 由约束传播落座 1
        m = map_seats({"0": 500.0, "1": 500.0},
                      [("A", 500.0), ("B", 500.0)],
                      anchors={"0": "A"})
        assert m == {"0": "A", "1": "B"}

    def test_exact_tier_resolves_close_stacks(self):
        # 分层语义(二次回归教训):栈值相近时纯容差会全歧义;exact 先行 →
        # seat0=100 与 A:100 精确唯一命中 → A 落座;B 由容差传播落 seat1
        m = map_seats({"0": 100.0, "1": 104.0},
                      [("A", 100.0), ("B", 102.0)])
        assert m == {"0": "A", "1": "B"}

    def test_close_stacks_both_drifted_left_unmapped(self):
        # 两座栈近 + 两边都漂移(无精确相等)→ 容差互为候选 → 宁缺毋错
        m = map_seats({"0": 101.0, "1": 103.0},
                      [("A", 100.0), ("B", 102.0)])
        assert m == {}

    def test_propagation_resolves(self):
        # C 只配 seat2 → 消去后 seat0/1 各剩唯一
        m = map_seats({"0": 100.0, "1": 200.0, "2": 900.0},
                      [("A", 103.0), ("B", 198.0), ("C", 902.0)])
        assert m == {"0": "A", "1": "B", "2": "C"}

    def test_missing_reads_no_edge(self):
        # 单边缺读 = 无边;有锚仍可救
        m = map_seats({"0": None, "1": 300.0},
                      [("A", None), ("B", 300.0)],
                      anchors={"0": "A"})
        assert m == {"0": "A", "1": "B"}

    def test_anchor_conflict_value_wins_by_order(self):
        # 锚指的玩家已被锚用过 → 不重复落座
        m = map_seats({"0": 500.0, "1": 300.0},
                      [("A", 500.0), ("B", 300.0)],
                      anchors={"0": "A", "1": "A"})
        assert m["0"] == "A" and m.get("1") == "B"
