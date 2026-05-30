"""HandPhase — Hand-level state machine (Phase 1.5 v3 §2.5).

Replaces implicit phase tracking (current code推 street from community card
count). Required by:

- R2 (SB/BB post ≠ voluntary action — needs BLIND_POSTING phase to suppress
  stack-delta-as-action signal)
- R9 (Showdown phase — timer all gone but hand not over)
- R10 (Street transitions — dealing_board 1-3s 动画 期间 OCR-1 idle)

Standalone — NOT integrated with orchestrator yet.

Maps to events/models.Street as follows:
    BLIND_POSTING / PREFLOP_ACTING       → Street.PREFLOP
    DEALING_FLOP / FLOP_ACTING            → Street.FLOP
    DEALING_TURN / TURN_ACTING            → Street.TURN
    DEALING_RIVER / RIVER_ACTING          → Street.RIVER
    SHOWDOWN                              → Street.SHOWDOWN
    BETWEEN_HANDS / DEALING_CARDS / SETTLING → no street (between hands)
"""

from __future__ import annotations

from enum import StrEnum

from events.models import Street


class HandPhase(StrEnum):
    # 跨手空窗期(R4 §3.4 盲点)
    BETWEEN_HANDS = "between_hands"
    DEALING_CARDS = "dealing_cards"      # 发底牌动画 (1-3s)
    # 主线阶段
    BLIND_POSTING = "blind_posting"      # SB/BB post (synthetic POST_SB/POST_BB)
    PREFLOP_ACTING = "preflop_acting"
    DEALING_FLOP = "dealing_flop"        # 翻牌发出动画 (1-3s)
    FLOP_ACTING = "flop_acting"
    DEALING_TURN = "dealing_turn"
    TURN_ACTING = "turn_acting"
    DEALING_RIVER = "dealing_river"
    RIVER_ACTING = "river_acting"
    SHOWDOWN = "showdown"                # 摊牌 — timer 全消但 hand 未结
    SETTLING = "settling"                # pot 分配 + 等下一手


# Linear forward path. Hand 内部一次性走完;between_hands → dealing_cards
# 是 hand 边界(可能跨多 tick 5-15s)
_FORWARD: dict[HandPhase, HandPhase] = {
    HandPhase.BETWEEN_HANDS: HandPhase.DEALING_CARDS,
    HandPhase.DEALING_CARDS: HandPhase.BLIND_POSTING,
    HandPhase.BLIND_POSTING: HandPhase.PREFLOP_ACTING,
    HandPhase.PREFLOP_ACTING: HandPhase.DEALING_FLOP,
    HandPhase.DEALING_FLOP: HandPhase.FLOP_ACTING,
    HandPhase.FLOP_ACTING: HandPhase.DEALING_TURN,
    HandPhase.DEALING_TURN: HandPhase.TURN_ACTING,
    HandPhase.TURN_ACTING: HandPhase.DEALING_RIVER,
    HandPhase.DEALING_RIVER: HandPhase.RIVER_ACTING,
    HandPhase.RIVER_ACTING: HandPhase.SHOWDOWN,
    HandPhase.SHOWDOWN: HandPhase.SETTLING,
    HandPhase.SETTLING: HandPhase.BETWEEN_HANDS,
}

# Early-end shortcuts: any acting phase can fold-around → SETTLING directly.
_EARLY_END_FROM: frozenset[HandPhase] = frozenset({
    HandPhase.PREFLOP_ACTING,
    HandPhase.FLOP_ACTING,
    HandPhase.TURN_ACTING,
    HandPhase.RIVER_ACTING,
})

# Phases where stack delta != voluntary action (suppress R2 misjudgment)
_STACK_DELTA_NOT_ACTION: frozenset[HandPhase] = frozenset({
    HandPhase.BLIND_POSTING,
    HandPhase.DEALING_CARDS,
    HandPhase.DEALING_FLOP,
    HandPhase.DEALING_TURN,
    HandPhase.DEALING_RIVER,
    HandPhase.SHOWDOWN,
    HandPhase.SETTLING,
    HandPhase.BETWEEN_HANDS,
})

# Phases where OCR-1 should idle (board-deal animation 1-3s)
_OCR_IDLE: frozenset[HandPhase] = frozenset({
    HandPhase.DEALING_CARDS,
    HandPhase.DEALING_FLOP,
    HandPhase.DEALING_TURN,
    HandPhase.DEALING_RIVER,
})

# Phases that map to a real Street(子集)
_TO_STREET: dict[HandPhase, Street] = {
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


class IllegalPhaseTransition(ValueError):
    pass


class HandPhaseMachine:
    """Drives the Hand-level state across the 12 phases of a single hand.

    Transitions are either `advance()` (linear forward) or `early_end()` (skip
    to SETTLING after fold-around).
    """

    def __init__(self):
        self._current = HandPhase.BETWEEN_HANDS

    @property
    def current(self) -> HandPhase:
        return self._current

    def advance(self) -> HandPhase:
        nxt = _FORWARD.get(self._current)
        if nxt is None:
            raise IllegalPhaseTransition(
                f"no forward transition from {self._current.value}"
            )
        self._current = nxt
        return self._current

    def force(self, target: HandPhase) -> None:
        """Force a transition (used for desync recovery from OCR signals).

        Skips validation — caller takes responsibility. Logged by caller.
        """
        self._current = target

    def early_end(self) -> HandPhase:
        """Fold-around shortcut: any *_ACTING → SETTLING."""
        if self._current not in _EARLY_END_FROM:
            raise IllegalPhaseTransition(
                f"early_end only valid from acting phases, got {self._current.value}"
            )
        self._current = HandPhase.SETTLING
        return self._current

    def stack_delta_is_action(self) -> bool:
        """R2 — should we treat seat stack delta as a voluntary action right now?

        Returns False during BLIND_POSTING / DEALING_* / SHOWDOWN / SETTLING /
        BETWEEN_HANDS — those are server-driven mechanics, not player actions.
        """
        return self._current not in _STACK_DELTA_NOT_ACTION

    def ocr_should_idle(self) -> bool:
        """R10 — should OCR-1 全局轮询 skip this tick (board-deal animation)?"""
        return self._current in _OCR_IDLE

    def to_street(self) -> Street | None:
        return _TO_STREET.get(self._current)
