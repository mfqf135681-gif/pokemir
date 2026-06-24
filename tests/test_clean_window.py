"""tests/test_clean_window.py — 阶段0 干净窗纯逻辑单测(stdlib only,Linux 可跑)。

避开 pipeline/__init__(拉 orchestrator/cv2/imagehash),直接加 pipeline/ 到 path
导入 clean_window(同 test_active_set 走 tools/ 的惯例)。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
from clean_window import (  # noqa: E402
    CleanWindowCapture,
    median,
    mode_longest,
)


# ── 纯函数 ────────────────────────────────────────────────
def test_median_ignores_none_and_handles_parity():
    assert median([]) is None
    assert median([None, None]) is None
    assert median([5.0]) == 5.0
    assert median([1.0, 100.0, 2.0]) == 2.0          # 奇数:中间值,抗单帧离群
    assert median([1.0, 3.0]) == 2.0                  # 偶数:均值
    assert median([None, 4.0, None, 6.0]) == 5.0      # 忽略 None


def test_mode_longest_tiebreak():
    assert mode_longest([]) is None
    assert mode_longest(["", ""]) is None
    assert mode_longest(["A", "A", "B"]) == "A"
    # 并列 → 取最长(多字符=OCR 更全,B-28)
    assert mode_longest(["小鬼", "小鬼徵熏"]) == "小鬼徵熏"


# ── 状态机 ────────────────────────────────────────────────
def test_basic_window_consensus():
    cw = CleanWindowCapture()
    # 结算前两 tick:忽略(不在窗)
    cw.tick(False, stacks={0: 999.0}, ids={0: "脏"})
    cw.tick(False, stacks={0: 999.0})
    # 结算后三 tick:累积
    cw.tick(True, stacks={0: 200.0, 1: 150.0}, ids={0: "Alice", 1: "Bob"})
    cw.tick(True, stacks={0: 200.0, 1: 150.0}, ids={0: "Alice", 1: "Bob"})
    cw.tick(True, stacks={0: 200.0, 1: 150.0}, ids={0: "Alice", 1: "Bob"})
    r = cw.finalize()
    assert r.fallback is False
    assert r.stacks == {0: 200.0, 1: 150.0}
    assert r.ids == {0: "Alice", 1: "Bob"}
    assert r.n_ticks == 3
    assert r.sample_counts == {0: 3, 1: 3}
    # 结算前的脏读 999/"脏" 不得污染
    assert 999.0 not in r.stacks.values()


def test_median_beats_single_stack_misread():
    cw = CleanWindowCapture()
    # 窗内一帧端点读爆(数字粘连),中位应忽略它
    cw.tick(True, stacks={3: 196.0})
    cw.tick(True, stacks={3: 19600.0})   # 单帧误读
    cw.tick(True, stacks={3: 196.0})
    r = cw.finalize()
    assert r.stacks[3] == 196.0


def test_id_mode_outvotes_overlay_garbage():
    # 多数帧是真名,个别帧 overlay 残留读成动作词 → 众数压制(治 TempUser 根因)
    cw = CleanWindowCapture()
    for name in ["吃米饭99", "吃米饭99", "跟注", "吃米饭99"]:
        cw.tick(True, ids={0: name})
    r = cw.finalize()
    assert r.ids[0] == "吃米饭99"


def test_no_settlement_fallback():
    # fold 到底无总底池 latch → 全程 settled=False → fallback,调用方走单帧兜底
    cw = CleanWindowCapture()
    cw.tick(False, stacks={0: 100.0})
    cw.tick(False, stacks={0: 100.0})
    r = cw.finalize()
    assert r.fallback is True
    assert r.stacks == {} and r.ids == {} and r.n_ticks == 0


def test_none_stacks_filtered_and_seat_dropped():
    cw = CleanWindowCapture()
    cw.tick(True, stacks={0: 50.0, 1: None})
    cw.tick(True, stacks={0: None, 1: None})
    r = cw.finalize()
    assert r.stacks == {0: 50.0}      # seat1 全 None → 不出现
    assert r.sample_counts.get(1, 0) == 0
    assert r.sample_counts[0] == 1


def test_settle_latches_through_subsequent_false():
    # 首次 settled=True 进窗后,即便后续帧传 False 仍保持在窗(latch 语义)
    cw = CleanWindowCapture()
    cw.tick(True, stacks={0: 10.0})
    cw.tick(False, stacks={0: 10.0})   # 仍应累积
    r = cw.finalize()
    assert r.n_ticks == 2
    assert r.sample_counts[0] == 2


def test_reset_isolates_next_hand():
    cw = CleanWindowCapture()
    cw.tick(True, stacks={0: 200.0}, ids={0: "A"})
    cw.finalize()
    cw.reset()
    # 下一手:无结算 → fallback,且不带上一手残留
    cw.tick(False, stacks={0: 300.0})
    r = cw.finalize()
    assert r.fallback is True
    assert r.stacks == {}
