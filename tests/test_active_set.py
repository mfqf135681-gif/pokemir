"""tests/test_active_set.py — active_set 纯逻辑单测(无 cv2,Linux 可跑)。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from active_set import active_intervals, active_set_at  # noqa: E402


def test_single_present_run():
    # 全程 hamming 低(像参考)→ 一段在手
    s = [(0, 2), (1, 3), (2, 1), (3, 4)]
    assert active_intervals(s, th=8) == [(0, 3)]


def test_fold_midway_splits():
    # 前段在手(低),t>=3 弃牌(hamming 跳高)→ 区间在 t2 结束
    s = [(0, 2), (1, 3), (2, 2), (3, 30), (4, 28), (5, 31)]
    assert active_intervals(s, th=8) == [(0, 2)]


def test_min_run_filters_single_frame_blip():
    # 单帧像参考(噪声)不算在手
    s = [(0, 30), (1, 2), (2, 30), (3, 30)]
    assert active_intervals(s, th=8, min_run=2) == []


def test_none_treated_as_absent():
    s = [(0, 2), (1, 2), (2, None), (3, 2), (4, 2)]
    assert active_intervals(s, th=8, min_run=2) == [(0, 1), (3, 4)]


def test_active_set_at():
    per_seat = {0: [(0, 30)], 1: [(0, 10), (20, 30)], 2: [(15, 25)]}
    assert active_set_at(5, per_seat) == {0, 1}      # seat2 还没进
    assert active_set_at(22, per_seat) == {0, 1, 2}  # 都覆盖
    assert active_set_at(12, per_seat) == {0}        # seat1 在(10,20)空档、seat2(15,)未进 → 仅 seat0


if __name__ == "__main__":
    test_single_present_run()
    test_fold_midway_splits()
    test_min_run_filters_single_frame_blip()
    test_none_treated_as_absent()
    test_active_set_at()
    print("✅ active_set 5/5 通过")
