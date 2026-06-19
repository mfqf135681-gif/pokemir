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


# ── build_facts ③:输家假弃牌(端点净亏≫已记投入 + 弃牌街后有下注)──────────
def test_build_facts_vetoes_loser_false_fold():
    """元元圆型:翻前弃但端点净亏42(只记 ante4),flop/turn 有下注 → 标 loser_false_fold。"""
    h = {"id": "t", "pot": 100, "init": {"6": 143, "5": 196, "4": 232},
         "fin": {"6": 101, "5": 303, "4": 190}, "xx": {}}
    seat_map = {"6": "元元圆", "5": "AAK", "4": "你瞅啥"}
    events = [
        ("元元圆", "preflop", "post_ante", 4, None),
        ("元元圆", "preflop", "fold", None, None),    # 假弃(实打到后面输42)
        ("AAK", "flop", "bet", 34, None),              # 弃牌街(preflop)之后有下注
        ("你瞅啥", "flop", "call", 34, None),
        ("AAK", "turn", "all_in", 158, None),
    ]
    facts = build_facts(h, events, seat_map, street_solver=False)
    assert ("loser_false_fold", "元元圆") in {(k, p) for k, p, _ in facts.veto_log}


def test_build_facts_keeps_fold_when_no_betting_after():
    """安全闸:翻前弃 + 端点净亏大,但 postflop 全过牌(无后续下注)→ 多出的亏无法由打后续街
    解释 → 判端点 misread,**不**否决真弃牌(不误删)。"""
    h = {"id": "t", "pot": 20, "init": {"6": 143, "5": 196},
         "fin": {"6": 101, "5": 150}, "xx": {}}
    seat_map = {"6": "元元圆", "5": "AAK"}
    events = [
        ("元元圆", "preflop", "post_ante", 4, None),
        ("元元圆", "preflop", "fold", None, None),
        ("AAK", "flop", "check", None, None),   # 只有 check,无下注
        ("AAK", "turn", "check", None, None),
    ]
    facts = build_facts(h, events, seat_map, street_solver=False)
    assert "loser_false_fold" not in {k for k, _, _ in facts.veto_log}


# ── 复盘时间线:被否决的原始行标 "🚫否决"(不隐藏,追加解释)──────────────
def test_timeline_marks_vetoed_rows():
    from solver.hand_repair import EXACT, HandFacts, RepairReport
    from solver.replay_view import build_solved_timeline
    facts = HandFacts(hand_id="t", pot_final=100, antes={}, street_contrib={}, nets={},
                      xx_winners=set(), folded_street={},
                      veto_log=[("winner_fold", "水上", "flop"),
                                ("false_all_in", "罗湖", "preflop"),
                                ("loser_false_fold", "元元圆", "preflop")])
    report = RepairReport(hand_id="t", status=EXACT, gap_before=0, gap_after=0)
    evs = [{"seq": 1, "street": "flop", "player": "水上", "action": "fold", "amount": None},
           {"seq": 2, "street": "preflop", "player": "罗湖", "action": "all_in", "amount": 1401},
           {"seq": 3, "street": "preflop", "player": "罗湖", "action": "call", "amount": 4},
           {"seq": 4, "street": "preflop", "player": "元元圆", "action": "fold", "amount": None}]
    tl = build_solved_timeline(facts, report, evs)
    rows = [r for s in tl["streets"] for r in s["rows"]]
    vetoed = {(r["player"], r["action"]) for r in rows if r["source"] == "🚫否决"}
    assert vetoed == {("水上", "fold"), ("罗湖", "all_in"), ("元元圆", "fold")}
    # 罗湖的 call(非目标)仍是"记录"
    assert any(r["player"] == "罗湖" and r["action"] == "call" and r["source"] == "记录"
               for r in rows)
    assert tl["vetoed_n"] == 3
