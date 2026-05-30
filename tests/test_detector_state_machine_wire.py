"""Integration test for Phase 1.5 v3.2 Step 2.1 — StateTracker wire phase.

Verifies that StateTracker now exposes T80 state machine fields
(`seat_lifecycle`, `hand_phase`), initialized correctly, and reset by
`start_new_hand`. NO assertions about reads/writes — those come in
Step 2.2 (mirror) and Step 2.3 (switch).
"""

import pytest

from pipeline.detector import StateTracker
from pipeline.state import (
    HandPhase,
    HandPhaseMachine,
    SeatLifecycle,
    SeatStateMachine,
)


class TestStateTrackerWireT80:
    def test_seat_lifecycle_wired(self):
        t = StateTracker()
        assert isinstance(t.seat_lifecycle, SeatStateMachine)
        # 9-seat default(party_poker_9 profile)
        for i in range(9):
            assert t.seat_lifecycle.get(i) == SeatLifecycle.EMPTY

    def test_hand_phase_wired(self):
        t = StateTracker()
        assert isinstance(t.hand_phase, HandPhaseMachine)
        assert t.hand_phase.current == HandPhase.BETWEEN_HANDS

    def test_start_new_hand_resets_state_machines(self):
        t = StateTracker()
        # Mutate the state machines via the public T80 API
        t.seat_lifecycle.transition_to(0, SeatLifecycle.ACTIVE)
        t.seat_lifecycle.transition_to(0, SeatLifecycle.FOLDED)
        t.seat_lifecycle.transition_to(1, SeatLifecycle.ACTIVE)
        t.seat_lifecycle.transition_to(1, SeatLifecycle.ALL_IN)
        t.hand_phase.advance()  # BETWEEN_HANDS → DEALING_CARDS
        t.hand_phase.advance()  # → BLIND_POSTING

        t.start_new_hand("test_table")

        # SeatLifecycle.reset_for_new_hand: FOLDED/ALL_IN → ACTIVE; EMPTY stays
        assert t.seat_lifecycle.get(0) == SeatLifecycle.ACTIVE
        assert t.seat_lifecycle.get(1) == SeatLifecycle.ACTIVE
        for i in range(2, 9):
            assert t.seat_lifecycle.get(i) == SeatLifecycle.EMPTY

        # HandPhaseMachine: fresh instance → BETWEEN_HANDS
        assert t.hand_phase.current == HandPhase.BETWEEN_HANDS

    def test_legacy_sets_unchanged(self):
        """Step 2.1 invariant: 旧 _folded_seats / _empty_seats 仍是 authoritative.

        ATTENTION_MODE=0 (默认) 时,新 state machine 字段只是初始化,
        不影响 legacy sets 的行为。
        """
        t = StateTracker()
        # Legacy sets exist + start empty
        assert t._folded_seats == set()
        assert t._empty_seats == set()
        # After start_new_hand: still empty (reset)
        t.start_new_hand("test")
        assert t._folded_seats == set()
        assert t._empty_seats == set()


class TestStateTrackerMirrorT91:
    """Step 2.2 — mirror_seat_state helper integration tests."""

    def test_mirror_to_sitting_out_from_empty(self):
        t = StateTracker()
        t._empty_seats.add(0)  # legacy
        t.mirror_seat_state(0, SeatLifecycle.SITTING_OUT)
        assert t.seat_lifecycle.get(0) == SeatLifecycle.SITTING_OUT
        assert t._empty_seats == {0}  # legacy unchanged

    def test_mirror_to_all_in_auto_promotes_from_empty(self):
        t = StateTracker()
        # EMPTY → ALL_IN not directly allowed; helper auto-promotes via ACTIVE
        t._went_all_in_this_hand.add(1)
        t.mirror_seat_state(1, SeatLifecycle.ALL_IN)
        assert t.seat_lifecycle.get(1) == SeatLifecycle.ALL_IN
        assert t._went_all_in_this_hand == {1}

    def test_mirror_to_folded_auto_promotes_from_empty(self):
        t = StateTracker()
        # EMPTY → FOLDED not directly allowed; helper auto-promotes via ACTIVE
        t._folded_seats.add(2)
        t.mirror_seat_state(2, SeatLifecycle.FOLDED)
        assert t.seat_lifecycle.get(2) == SeatLifecycle.FOLDED
        assert t._folded_seats == {2}

    def test_mirror_from_active_to_folded(self):
        t = StateTracker()
        # Seat 3: pre-set to ACTIVE (simulating prior promotion)
        t.seat_lifecycle.transition_to(3, SeatLifecycle.ACTIVE)
        t._folded_seats.add(3)
        t.mirror_seat_state(3, SeatLifecycle.FOLDED)
        assert t.seat_lifecycle.get(3) == SeatLifecycle.FOLDED

    def test_mirror_swallows_illegal_transition_silently(self):
        """LEAVING → ACTIVE 不合法,但 mirror 应吞噬不抛."""
        t = StateTracker()
        t.seat_lifecycle.transition_to(4, SeatLifecycle.ACTIVE)
        t.seat_lifecycle.transition_to(4, SeatLifecycle.LEAVING)
        # Now LEAVING → ACTIVE is illegal; mirror should swallow.
        t.mirror_seat_state(4, SeatLifecycle.ACTIVE)
        # State unchanged (still LEAVING)
        assert t.seat_lifecycle.get(4) == SeatLifecycle.LEAVING

    def test_mirror_self_transition_is_noop(self):
        t = StateTracker()
        t.seat_lifecycle.transition_to(5, SeatLifecycle.ACTIVE)
        t.mirror_seat_state(5, SeatLifecycle.ACTIVE)  # 同状态
        assert t.seat_lifecycle.get(5) == SeatLifecycle.ACTIVE


class TestStateTrackerIsSkippableT92:
    """Step 2.3 — is_skippable_seat ATTENTION_MODE-gated read."""

    def test_legacy_mode_reads_from_sets(self, monkeypatch):
        """ATTENTION_MODE=0 (默认):is_skippable_seat 读旧 sets."""
        import config
        monkeypatch.setattr(config, "ATTENTION_MODE", False)
        t = StateTracker()
        # Initially none skippable
        for i in range(9):
            assert not t.is_skippable_seat(i)
        # Add to legacy sets
        t._folded_seats.add(0)
        t._empty_seats.add(1)
        assert t.is_skippable_seat(0)
        assert t.is_skippable_seat(1)
        # seat_lifecycle 不参与判断(legacy 模式)
        t.seat_lifecycle.transition_to(2, SeatLifecycle.ACTIVE)
        t.seat_lifecycle.transition_to(2, SeatLifecycle.FOLDED)
        assert not t.is_skippable_seat(2)  # legacy 模式不看 seat_lifecycle

    def test_attention_mode_reads_from_state_machine(self, monkeypatch):
        """ATTENTION_MODE=1:is_skippable_seat 读 seat_lifecycle.is_skippable.

        T94 fix: EMPTY 默认 NOT skippable(语义改为"未知,默认 OCR").
        """
        import config
        monkeypatch.setattr(config, "ATTENTION_MODE", True)
        t = StateTracker()
        # T94: EMPTY 不再 skip(跟 legacy mode 行为对齐)
        for i in range(9):
            assert not t.is_skippable_seat(i)  # EMPTY → NOT skippable
        # Activate seat 0(显式)
        t.seat_lifecycle.transition_to(0, SeatLifecycle.ACTIVE)
        assert not t.is_skippable_seat(0)  # ACTIVE 不跳
        # Fold seat 0
        t.seat_lifecycle.transition_to(0, SeatLifecycle.FOLDED)
        assert t.is_skippable_seat(0)  # FOLDED 跳
        # SITTING_OUT 才真跳(已确认的)
        t.seat_lifecycle.transition_to(1, SeatLifecycle.SITTING_OUT)
        assert t.is_skippable_seat(1)
        # 旧 sets 不参与判断(attention 模式)
        t._folded_seats.add(2)
        # seat 2 EMPTY → NOT skippable(attention mode 不看 _folded_seats)
        assert not t.is_skippable_seat(2)

    def test_both_modes_consistent_after_full_mirror(self, monkeypatch):
        """Mirror 写后,两模式应给相同 skip 答案(共识检查)."""
        import config
        t = StateTracker()
        # 模拟 fold seat 3 + empty seat 4(经 mirror)
        t._folded_seats.add(3)
        t.mirror_seat_state(3, SeatLifecycle.FOLDED)
        t._empty_seats.add(4)
        t.mirror_seat_state(4, SeatLifecycle.SITTING_OUT)
        # Active seat 5 (mirror only)
        t.seat_lifecycle.transition_to(5, SeatLifecycle.ACTIVE)

        # seat 6 留 EMPTY(测试 T94 fix:EMPTY 跟 legacy mode 空集等价 NOT skippable)
        for mode in (False, True):
            monkeypatch.setattr(config, "ATTENTION_MODE", mode)
            assert t.is_skippable_seat(3), f"seat 3 should skip in mode={mode}"
            assert t.is_skippable_seat(4), f"seat 4 should skip in mode={mode}"
            # seat 5: legacy 模式 EMPTY-by-default→不在 sets→不跳;
            # attention 模式 ACTIVE→不跳。两模式一致。
            assert not t.is_skippable_seat(5), f"seat 5 should NOT skip in mode={mode}"
            # seat 6: 未初始化 EMPTY,两模式都应 NOT skippable(T94 fix 核心)
            assert not t.is_skippable_seat(6), \
                f"seat 6 EMPTY should NOT skip in mode={mode} (T94 fix)"
