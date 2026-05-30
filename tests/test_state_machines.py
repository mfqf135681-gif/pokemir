"""Unit tests for Phase 1.5 v3 standalone state machines.

Linux-only smoke test — does NOT exercise orchestrator integration (that's
deferred to Win 端 verify per §11.4 Step 2).
"""

import pytest

from events.models import Street
from pipeline.state import (
    HandPhase,
    HandPhaseMachine,
    SeatLifecycle,
    SeatStateMachine,
)
from pipeline.state.hand_phase import IllegalPhaseTransition
from pipeline.state.seat_lifecycle import IllegalTransition


# ─── SeatLifecycle ─────────────────────────────────────────────────────

class TestSeatLifecycle:
    def test_initial_state_all_empty(self):
        m = SeatStateMachine(n_seats=9)
        for i in range(9):
            assert m.get(i) == SeatLifecycle.EMPTY

    def test_legal_transition(self):
        m = SeatStateMachine(n_seats=2)
        m.transition_to(0, SeatLifecycle.ACTIVE)
        assert m.get(0) == SeatLifecycle.ACTIVE
        m.transition_to(0, SeatLifecycle.FOLDED)
        assert m.get(0) == SeatLifecycle.FOLDED

    def test_illegal_transition_raises(self):
        m = SeatStateMachine(n_seats=2)
        # EMPTY → FOLDED 不合法(必须先 ACTIVE)
        with pytest.raises(IllegalTransition):
            m.transition_to(0, SeatLifecycle.FOLDED)

    def test_self_transition_is_noop(self):
        m = SeatStateMachine(n_seats=2)
        m.transition_to(0, SeatLifecycle.ACTIVE)
        m.transition_to(0, SeatLifecycle.ACTIVE)  # 不报错
        assert m.get(0) == SeatLifecycle.ACTIVE

    def test_empty_is_NOT_skippable(self):
        """T94 fix: EMPTY = 未知/未初始化,默认 OCR 一次确认(跟 legacy mode 空集等价).

        真 sit-out 由 _detect_empty_seats 触发 mirror → SITTING_OUT(才跳过).
        """
        m = SeatStateMachine(n_seats=4)
        for i in range(4):
            assert m.get(i) == SeatLifecycle.EMPTY
            assert not m.is_skippable(i), f"EMPTY seat {i} should NOT be skippable"
        assert m.skippable_seats() == set()
        assert m.active_seats() == set()  # EMPTY 不算 active 也不算 skippable

    def test_skippable_states_match_spec(self):
        """OCR-1 全局轮询应跳过 4 类(R3 §3.3)+ leaving.

        T94 fix: EMPTY 不在 _SKIPPABLE 内(未知 → 默认 OCR).
        """
        m = SeatStateMachine(n_seats=6)
        # Set up: ACTIVE, ACTIVE, FOLDED, ALL_IN, SIT_OUT, LEAVING
        for i in range(2):
            m.transition_to(i, SeatLifecycle.ACTIVE)
        m.transition_to(2, SeatLifecycle.ACTIVE)
        m.transition_to(2, SeatLifecycle.FOLDED)
        m.transition_to(3, SeatLifecycle.ACTIVE)
        m.transition_to(3, SeatLifecycle.ALL_IN)
        m.transition_to(4, SeatLifecycle.SITTING_OUT)
        m.transition_to(5, SeatLifecycle.SITTING_OUT)
        m.transition_to(5, SeatLifecycle.LEAVING)

        assert m.skippable_seats() == {2, 3, 4, 5}
        assert m.active_seats() == {0, 1}
        for i in (2, 3, 4, 5):
            assert m.is_skippable(i)
        for i in (0, 1):
            assert not m.is_skippable(i)

    def test_reset_for_new_hand_reactivates_terminals(self):
        m = SeatStateMachine(n_seats=4)
        m.transition_to(0, SeatLifecycle.ACTIVE)
        m.transition_to(0, SeatLifecycle.FOLDED)
        m.transition_to(1, SeatLifecycle.ACTIVE)
        m.transition_to(1, SeatLifecycle.ALL_IN)
        m.transition_to(2, SeatLifecycle.SITTING_OUT)
        # seat 3 stays EMPTY

        m.reset_for_new_hand()

        assert m.get(0) == SeatLifecycle.ACTIVE  # FOLDED → ACTIVE
        assert m.get(1) == SeatLifecycle.ACTIVE  # ALL_IN → ACTIVE
        assert m.get(2) == SeatLifecycle.SITTING_OUT  # 不动
        assert m.get(3) == SeatLifecycle.EMPTY  # 不动

    def test_leaving_to_empty(self):
        m = SeatStateMachine(n_seats=2)
        m.transition_to(0, SeatLifecycle.ACTIVE)
        m.transition_to(0, SeatLifecycle.LEAVING)
        m.transition_to(0, SeatLifecycle.EMPTY)
        assert m.get(0) == SeatLifecycle.EMPTY

    def test_leaving_cannot_go_back_to_active(self):
        m = SeatStateMachine(n_seats=2)
        m.transition_to(0, SeatLifecycle.ACTIVE)
        m.transition_to(0, SeatLifecycle.LEAVING)
        with pytest.raises(IllegalTransition):
            m.transition_to(0, SeatLifecycle.ACTIVE)

    def test_snapshot_is_copy(self):
        m = SeatStateMachine(n_seats=2)
        snap = m.snapshot()
        m.transition_to(0, SeatLifecycle.ACTIVE)
        assert snap[0] == SeatLifecycle.EMPTY  # snapshot 不受后续变化影响


# ─── HandPhase ─────────────────────────────────────────────────────────

class TestHandPhase:
    def test_initial_phase_is_between_hands(self):
        m = HandPhaseMachine()
        assert m.current == HandPhase.BETWEEN_HANDS

    def test_linear_forward_completes_full_hand(self):
        m = HandPhaseMachine()
        expected_path = [
            HandPhase.DEALING_CARDS,
            HandPhase.BLIND_POSTING,
            HandPhase.PREFLOP_ACTING,
            HandPhase.DEALING_FLOP,
            HandPhase.FLOP_ACTING,
            HandPhase.DEALING_TURN,
            HandPhase.TURN_ACTING,
            HandPhase.DEALING_RIVER,
            HandPhase.RIVER_ACTING,
            HandPhase.SHOWDOWN,
            HandPhase.SETTLING,
            HandPhase.BETWEEN_HANDS,  # cycle 回到起点
        ]
        for expected in expected_path:
            assert m.advance() == expected

    def test_early_end_from_acting_phases(self):
        for acting in (HandPhase.PREFLOP_ACTING, HandPhase.FLOP_ACTING,
                       HandPhase.TURN_ACTING, HandPhase.RIVER_ACTING):
            m = HandPhaseMachine()
            m.force(acting)
            assert m.early_end() == HandPhase.SETTLING

    def test_early_end_from_non_acting_raises(self):
        m = HandPhaseMachine()
        m.force(HandPhase.BLIND_POSTING)
        with pytest.raises(IllegalPhaseTransition):
            m.early_end()

    def test_stack_delta_is_action_only_during_acting(self):
        """R2 — BLIND_POSTING / dealing / showdown / settling 期间 stack 减 ≠ action."""
        m = HandPhaseMachine()

        not_action_phases = [
            HandPhase.BLIND_POSTING,
            HandPhase.DEALING_CARDS,
            HandPhase.DEALING_FLOP,
            HandPhase.DEALING_TURN,
            HandPhase.DEALING_RIVER,
            HandPhase.SHOWDOWN,
            HandPhase.SETTLING,
            HandPhase.BETWEEN_HANDS,
        ]
        for ph in not_action_phases:
            m.force(ph)
            assert not m.stack_delta_is_action(), f"{ph.value} should suppress"

        action_phases = [
            HandPhase.PREFLOP_ACTING,
            HandPhase.FLOP_ACTING,
            HandPhase.TURN_ACTING,
            HandPhase.RIVER_ACTING,
        ]
        for ph in action_phases:
            m.force(ph)
            assert m.stack_delta_is_action(), f"{ph.value} should treat as action"

    def test_ocr_idle_during_board_deal(self):
        """R10 — dealing_* 期间 OCR-1 should idle."""
        m = HandPhaseMachine()

        idle_phases = [
            HandPhase.DEALING_CARDS,
            HandPhase.DEALING_FLOP,
            HandPhase.DEALING_TURN,
            HandPhase.DEALING_RIVER,
        ]
        for ph in idle_phases:
            m.force(ph)
            assert m.ocr_should_idle()

        # All other phases: OCR should NOT idle
        for ph in HandPhase:
            if ph in idle_phases:
                continue
            m.force(ph)
            assert not m.ocr_should_idle(), f"{ph.value} should not idle"

    def test_to_street_mapping(self):
        m = HandPhaseMachine()

        cases = {
            HandPhase.BLIND_POSTING: Street.PREFLOP,
            HandPhase.PREFLOP_ACTING: Street.PREFLOP,
            HandPhase.DEALING_FLOP: Street.FLOP,
            HandPhase.FLOP_ACTING: Street.FLOP,
            HandPhase.DEALING_TURN: Street.TURN,
            HandPhase.TURN_ACTING: Street.TURN,
            HandPhase.DEALING_RIVER: Street.RIVER,
            HandPhase.RIVER_ACTING: Street.RIVER,
            HandPhase.SHOWDOWN: Street.SHOWDOWN,
        }
        for ph, expected_street in cases.items():
            m.force(ph)
            assert m.to_street() == expected_street

        # No-street phases
        for ph in (HandPhase.BETWEEN_HANDS, HandPhase.DEALING_CARDS, HandPhase.SETTLING):
            m.force(ph)
            assert m.to_street() is None

    def test_advance_from_between_hands_loops(self):
        """SETTLING → BETWEEN_HANDS → DEALING_CARDS = ready for next hand."""
        m = HandPhaseMachine()
        m.force(HandPhase.SETTLING)
        assert m.advance() == HandPhase.BETWEEN_HANDS
        assert m.advance() == HandPhase.DEALING_CARDS
