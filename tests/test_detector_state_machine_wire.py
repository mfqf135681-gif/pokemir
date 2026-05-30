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
