"""tests/test_truth_score.py — 真值对账 harness 可测纯核单测(stdlib only,Linux 可跑)。

避开 pipeline/__init__,直接加 tools/ 到 path(同 test_active_set 惯例)。
只测纯核(amount_match / score_hand / match_hands / aggregate / db_row_to_norm);
DB 适配器 fetch_db_hands 需 psycopg2+库,不在此测。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from truth_score import (  # noqa: E402
    NormHand,
    amount_match,
    score_hand,
    match_hands,
    aggregate,
    truth_to_norm,
    db_row_to_norm,
)


# ── amount_match ──────────────────────────────────────────
def test_amount_match():
    assert amount_match(None, None) is True            # check/fold
    assert amount_match(None, 4.0) is False            # 一方缺
    assert amount_match(100.0, 101.0) is True          # abs≤2
    assert amount_match(100.0, 104.0) is True          # rel 5%
    assert amount_match(100.0, 120.0) is False         # 超 5%
    assert amount_match(4.0, 5.0) is True              # 小额走 abs_tol=2


# ── score_hand ────────────────────────────────────────────
def _h(actions, seats=None, winners=None):
    return NormHand(seats=seats or {}, actions=actions, winners=winners or [])


def test_action_recall_precision_perfect():
    acts = [
        {"seat": 0, "street": "preflop", "action": "call", "amount": 4},
        {"seat": 2, "street": "preflop", "action": "raise", "amount": 16},
    ]
    s = score_hand(_h(acts), _h(list(acts)))
    assert s["action_recall"] == 1.0
    assert s["action_precision"] == 1.0
    assert s["action_misamount"] == 0


def test_action_missing_lowers_recall():
    truth = _h([
        {"seat": 0, "street": "preflop", "action": "call", "amount": 4},
        {"seat": 2, "street": "flop", "action": "bet", "amount": 24},
    ])
    db = _h([{"seat": 0, "street": "preflop", "action": "call", "amount": 4}])
    s = score_hand(truth, db)
    assert s["action_recall"] == 0.5     # 漏 flop bet
    assert s["action_precision"] == 1.0


def test_action_misamount_counted_separately():
    # seat/street/action 对上,但金额差太多 → misamount,不算 matched
    truth = _h([{"seat": 2, "street": "flop", "action": "bet", "amount": 24}])
    db = _h([{"seat": 2, "street": "flop", "action": "bet", "amount": 240}])
    s = score_hand(truth, db)
    assert s["action_matched"] == 0
    assert s["action_misamount"] == 1
    assert s["action_recall"] == 0.0


def test_endpoint_stack_error_and_missing():
    truth = NormHand(
        seats={0: {"name": "A", "initial": 200, "final": 196},
               2: {"name": "B", "initial": 300, "final": 412},
               5: {"name": "C", "initial": 150, "final": 38}},
        actions=[], winners=[2])
    db = NormHand(
        seats={0: {"name": "A", "initial": 200, "final": 196},   # 准
               2: {"name": "B", "initial": 300, "final": 999},   # final 错
               5: {"name": "C", "initial": 150, "final": None}}, # 缺 final
        actions=[], winners=[2])
    s = score_hand(truth, db)
    fs = s["final_stack"]
    assert fs["n"] == 3
    assert fs["covered"] == 2          # seat5 final 缺
    assert fs["missing"] == 1
    assert fs["within_tol"] == 1       # 只有 seat0 在容差内
    assert s["initial_stack"]["within_tol"] == 3


def test_stack_tolerance_tightened():
    # 大栈 10 筹码误差:旧 5% 相对会判命中;新默认 ±2 绝对应判【不命中】(治松尺测不出干净窗改善)
    truth = NormHand(seats={0: {"name": "A", "initial": 2000, "final": 1990}}, actions=[], winners=[])
    db = NormHand(seats={0: {"name": "A", "initial": 2000, "final": 2000}}, actions=[], winners=[])  # final 差10
    s = score_hand(truth, db)  # 默认 stack_abs_tol=2, stack_rel_tol=0
    assert s["final_stack"]["within_tol"] == 0       # 10>2 → 收紧后不命中
    assert s["final_stack"]["errors"] == [10.0]
    s2 = score_hand(truth, db, stack_abs_tol=2, stack_rel_tol=0.05)
    assert s2["final_stack"]["within_tol"] == 1      # 放宽相对:10 ≤ 5%*2000=100 → 命中


def test_aggregate_err_dist():
    t = NormHand(seats={0: {"name": "A", "initial": 100, "final": 100},
                        1: {"name": "B", "initial": 100, "final": 100}}, actions=[], winners=[])
    d = NormHand(seats={0: {"name": "A", "initial": 100, "final": 104},    # final err 4
                        1: {"name": "B", "initial": 100, "final": 110}},   # final err 10
                 actions=[], winners=[])
    agg = aggregate([score_hand(t, d)])
    fe = agg["final_err_dist"]
    assert fe["n_seats"] == 2
    assert fe["mean"] == 7.0          # (4+10)/2
    assert fe["max"] == 10.0
    assert agg["initial_err_dist"]["max"] == 0.0   # initial 全对


def test_winner_scoring():
    s = score_hand(_h([], winners=[2]), _h([], winners=[2]))
    assert s["winner_exact"] is True and s["winner_jaccard"] == 1.0
    s2 = score_hand(_h([], winners=[2]), _h([], winners=[2, 5]))
    assert s2["winner_exact"] is False and s2["winner_jaccard"] == 0.5


def test_forced_posts_excluded_from_action_scoring():
    # post_sb/bb 不算自愿动作,不进召回/精度分母
    truth = _h([
        {"seat": 0, "street": "preflop", "action": "post_sb", "amount": 2},
        {"seat": 1, "street": "preflop", "action": "call", "amount": 4},
    ])
    db = _h([{"seat": 1, "street": "preflop", "action": "call", "amount": 4}])
    s = score_hand(truth, db)
    assert s["action_truth_n"] == 1     # 只数 call
    assert s["action_recall"] == 1.0


# ── match_hands ───────────────────────────────────────────
def test_match_hands_consistency_flags():
    t = [NormHand({}, [], [], button_seat=2, blinds={"sb": 2, "bb": 4}),
         NormHand({}, [], [], button_seat=3, blinds={"sb": 2, "bb": 4})]
    d = [NormHand({}, [], [], button_seat=2, blinds={"sb": 2, "bb": 4}),
         NormHand({}, [], [], button_seat=5, blinds={"sb": 2, "bb": 4})]  # 第2手按钮错位
    pairs = match_hands(t, d)
    assert pairs[0]["consistency_flags"] == []
    assert any("button_mismatch" in f for f in pairs[1]["consistency_flags"])


def test_match_hands_count_mismatch():
    t = [NormHand({}, [], [])]
    d = [NormHand({}, [], []), NormHand({}, [], [])]
    pairs = match_hands(t, d)
    assert any("COUNT_MISMATCH" in f for f in pairs[-1]["consistency_flags"])


# ── aggregate ─────────────────────────────────────────────
def test_aggregate():
    s1 = score_hand(
        _h([{"seat": 0, "street": "preflop", "action": "call", "amount": 4}]),
        _h([{"seat": 0, "street": "preflop", "action": "call", "amount": 4}]))
    s2 = score_hand(
        _h([{"seat": 0, "street": "preflop", "action": "bet", "amount": 10}]),
        _h([]))  # 全漏
    agg = aggregate([s1, s2])
    assert agg["hands"] == 2
    assert agg["action_recall_avg"] == 0.5    # (1.0 + 0.0)/2


# ── 真值/DB 归一化 ────────────────────────────────────────
def test_truth_to_norm_seat_keys_int():
    h = {"seats": {"0": {"name": "A", "initial": 200, "final": 196}},
         "actions": [{"seat": "0", "street": "preflop", "action": "call", "amount": 4}],
         "winners": [{"seat": 2, "amount": 120}], "button_seat": 2}
    n = truth_to_norm(h)
    assert 0 in n.seats and n.seats[0]["name"] == "A"
    assert n.actions[0]["seat"] == 0
    assert n.winners == [2]


def test_db_row_to_norm():
    hand_row = {
        "raw_data": {
            "player_stacks_initial": {"0": 200, "2": 300},
            "player_stacks_final": {"0": 196, "2": 412},
            "seat_names": {"0": "A", "2": "B"},
            "blind_level": {"sb": 2, "bb": 4, "ante": 4},
        },
        "result": {"winners_endpoint": [2]},
    }
    action_rows = [
        {"raw_data": {"seat_index": 0}, "action_type": "call", "amount": 4, "street": "preflop"},
        {"raw_data": {}, "action_type": "fold", "amount": None, "street": "preflop"},  # 无 seat_index→跳
    ]
    n = db_row_to_norm(hand_row, action_rows)
    assert n.seats[0] == {"name": "A", "initial": 200, "final": 196}
    assert n.winners == [2]
    assert len(n.actions) == 1 and n.actions[0]["seat"] == 0
    assert n.blinds["bb"] == 4
