"""#226 端点否决 — 纯函数 + build_facts 整合单测(Linux 可跑,无 live/cv2 依赖)。

覆盖:① 假全下/赢家误弃被剔;② 真全下(爆栈)/真弃牌(输家)不被误剔(安全性);
③ build_facts 整合后 folded_street/contrib 干净 + veto_log 记账。
种子取自 2026-06-10 实战 A 手(水上真全下赢家被记弃牌、罗湖假全下 net-545 终栈860)。
"""
from solver.hand_repair import veto_events_by_endpoint
from tools.solve_hands import build_facts


# ── 纯函数:假事件被剔 ───────────────────────────────────────────────
def test_vetoes_false_allin_and_winner_fold():
    events = [
        ("罗湖", "preflop", "all_in", 1401, None),  # 假全下(net-545、终栈860=未投光)
        ("水上", "flop", "fold", None, None),         # 赢家误弃(net+506)
        ("水上", "preflop", "call", 4, None),         # 正常,留
        ("罗湖", "preflop", "call", 4, None),         # 正常,留
    ]
    nets = {"水上": 506.0, "罗湖": -545.0}
    final = {"水上": 1039.0, "罗湖": 860.0}
    kept, log = veto_events_by_endpoint(events, nets, xx_winners=set(), final=final)
    kinds = {(k, p) for k, p, _ in log}
    assert ("false_all_in", "罗湖") in kinds
    assert ("winner_fold", "水上") in kinds
    assert all(not (e[0] == "罗湖" and e[2] == "all_in") for e in kept)
    assert all(not (e[0] == "水上" and e[2] == "fold") for e in kept)
    assert any(e[2] == "call" for e in kept)   # 非目标动作保留


# ── 安全性:真事件【不】被误剔 ───────────────────────────────────────
def test_keeps_real_allin_loser_busted():
    """真全下输家(爆栈、终栈≈0)不该被当假全下剔。"""
    events = [("X", "turn", "all_in", 500, None)]
    kept, log = veto_events_by_endpoint(events, nets={"X": -500.0}, xx_winners=set(),
                                        final={"X": 0.0})
    assert log == [] and len(kept) == 1


def test_keeps_real_allin_winner():
    """真全下赢家(net>0,投光后赢回)不该被剔。"""
    events = [("W", "river", "all_in", 600, None)]
    kept, log = veto_events_by_endpoint(events, nets={"W": 720.0}, xx_winners=set(),
                                        final={"W": 1440.0})
    assert log == [] and len(kept) == 1


def test_keeps_real_fold_by_loser():
    """输家(net<0)的真弃牌不该被剔——只有赢家的弃牌才矛盾。"""
    events = [("L", "flop", "fold", None, None)]
    kept, log = veto_events_by_endpoint(events, nets={"L": -8.0}, xx_winners=set(),
                                        final={"L": 600.0})
    assert log == [] and len(kept) == 1


# ── build_facts 整合:facts 干净 + veto_log 记账 ─────────────────────
def test_build_facts_applies_veto():
    h = {"id": "test_A", "pot": 1984,
         "init": {"0": 533, "1": 1405}, "fin": {"0": 1039, "1": 860}, "xx": {}}
    seat_map = {"0": "水上", "1": "罗湖"}
    events = [
        ("罗湖", "preflop", "post_ante", 4, None),
        ("水上", "preflop", "post_ante", 4, None),
        ("罗湖", "preflop", "all_in", 1401, None),   # 假全下
        ("水上", "preflop", "call", 4, None),
        ("水上", "flop", "fold", None, None),          # 赢家误弃
    ]
    facts = build_facts(h, events, seat_map, street_solver=False)
    # 赢家(水上 net+506)不该出现在 folded_street
    assert "水上" not in facts.folded_street, f"赢家不该被记弃牌,实 {facts.folded_street}"
    # 假全下被剔 → 罗湖 不应有 ~1401 的巨额 contrib(只剩 ante)
    assert facts.recorded("罗湖") < 100, f"假全下1401应被剔,实 recorded={facts.recorded('罗湖')}"
    # veto_log 两条都在
    kinds = {(k, p) for k, p, _ in facts.veto_log}
    assert ("false_all_in", "罗湖") in kinds and ("winner_fold", "水上") in kinds


def test_build_facts_veto_off_keeps_dirty():
    """veto=False 时回到旧行为(脏事件保留)——证明开关有效、可回退。"""
    h = {"id": "t", "pot": 100, "init": {"0": 533}, "fin": {"0": 1039}, "xx": {}}
    seat_map = {"0": "水上"}
    events = [("水上", "flop", "fold", None, None)]
    facts = build_facts(h, events, seat_map, street_solver=False, veto=False)
    assert "水上" in facts.folded_street   # 关闭否决 → 脏 fold 仍在
