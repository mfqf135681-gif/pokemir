"""#235 摊牌闸 — 纯判据单测(Linux,无 cv2)。

is_showdown_runout:行动是否已结束(进入亮牌跑马)→ 调用方据此冻结弃牌救援,
治"全下/摊牌亮牌时牌背消失被误判弃牌"(占假弃大头,2026-06-15 审计)。
"""
from pipeline.reconstruct import is_showdown_runout


def test_headsup_allin_plus_caller():
    # 1 全下 + 1 带码跟注方:非全下live=1 → runout(治跟注方亮牌假弃)
    assert is_showdown_runout({0, 1}, set(), {0}) is True


def test_multiway_one_allin_others_live():
    # 1 全下 + 3 带码:非全下live=3 → 不 runout(那3家还能真弃,治多人边池反例)
    assert is_showdown_runout({0, 1, 2, 3}, set(), {0}) is False


def test_all_seats_allin():
    assert is_showdown_runout({0, 1, 2}, set(), {0, 1, 2}) is True


def test_no_allin_normal_hand():
    # 无全下 → 普通手,救援照常(不冻结)
    assert is_showdown_runout({0, 1, 2}, {2}, set()) is False


def test_two_allin_one_live():
    assert is_showdown_runout({0, 1, 2}, set(), {0, 1}) is True


def test_folded_seats_excluded():
    # 5 发牌,3 弃,剩 1 全下 + 1 带码 → runout(heads-up 全下对峙)
    assert is_showdown_runout({0, 1, 2, 3, 4}, {2, 3, 4}, {0}) is True
