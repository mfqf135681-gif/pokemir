"""SeatLifecycle — 5-state seat state machine (Phase 1.5 v3).

Replaces flat `_folded_seats` + `_empty_seats` sets in HandStateTracker with
an explicit per-seat lifecycle. Designed to satisfy:

- R3 (skip 4 类 — folded + all_in + sit_out + empty)
- R5 (sit-out vs fold vs leave 区分)
- 4 加法陷阱 §11.3 #1 (强制删旧 sets,不双轨)

Standalone — NOT integrated with orchestrator yet.
"""

from __future__ import annotations

from enum import StrEnum


class SeatLifecycle(StrEnum):
    EMPTY = "empty"              # 无人坐
    SITTING_OUT = "sitting_out"  # 坐着但本手不发牌(sit-out toggle)
    ACTIVE = "active"            # 在 hand 内,可能在等行动或可行动
    FOLDED = "folded"            # 本手已弃牌(终态,until between_hands)
    ALL_IN = "all_in"            # 本手 all-in(终态,但仍在 pot 等摊牌)
    LEAVING = "leaving"          # 准备离桌(下手不发牌)


# Transitions allowed within / across hand. Source → set of allowed targets.
_TRANSITIONS: dict[SeatLifecycle, set[SeatLifecycle]] = {
    SeatLifecycle.EMPTY: {
        SeatLifecycle.SITTING_OUT,  # 新玩家入座但本手不参与
        SeatLifecycle.ACTIVE,        # 新玩家入座且赶上发牌
    },
    SeatLifecycle.SITTING_OUT: {
        SeatLifecycle.ACTIVE,        # 下手开始参与
        SeatLifecycle.LEAVING,
        SeatLifecycle.EMPTY,
    },
    SeatLifecycle.ACTIVE: {
        SeatLifecycle.FOLDED,
        SeatLifecycle.ALL_IN,
        SeatLifecycle.SITTING_OUT,   # 中途 sit out(极少见)
        SeatLifecycle.LEAVING,
    },
    SeatLifecycle.FOLDED: {
        SeatLifecycle.ACTIVE,        # 新一手开始
        SeatLifecycle.SITTING_OUT,
        SeatLifecycle.LEAVING,
        SeatLifecycle.EMPTY,
    },
    SeatLifecycle.ALL_IN: {
        SeatLifecycle.ACTIVE,        # 新一手 + 仍有筹码
        SeatLifecycle.SITTING_OUT,
        SeatLifecycle.LEAVING,
        SeatLifecycle.EMPTY,          # 输光后离桌
    },
    SeatLifecycle.LEAVING: {
        SeatLifecycle.EMPTY,
    },
}

# Seats in these states should be skipped by OCR-1 全局轮询 + per-seat action loop.
_SKIPPABLE: frozenset[SeatLifecycle] = frozenset({
    SeatLifecycle.EMPTY,
    SeatLifecycle.SITTING_OUT,
    SeatLifecycle.FOLDED,
    SeatLifecycle.ALL_IN,
    SeatLifecycle.LEAVING,
})


class IllegalTransition(ValueError):
    """Raised when a state transition is not allowed by the lifecycle."""


class SeatStateMachine:
    """Per-table lifecycle tracker keyed by seat index.

    Replaces the flat `_folded_seats` / `_empty_seats` sets. Each seat has
    exactly one state; transitions go through `transition_to()` which validates
    against the allowed-transitions table.
    """

    def __init__(self, n_seats: int = 9):
        self._n_seats = n_seats
        self._states: dict[int, SeatLifecycle] = {
            i: SeatLifecycle.EMPTY for i in range(n_seats)
        }

    def get(self, seat_index: int) -> SeatLifecycle:
        return self._states[seat_index]

    def transition_to(self, seat_index: int, target: SeatLifecycle) -> None:
        current = self._states[seat_index]
        if target == current:
            return
        allowed = _TRANSITIONS.get(current, set())
        if target not in allowed:
            raise IllegalTransition(
                f"seat_{seat_index}: {current.value} → {target.value} not allowed "
                f"(allowed: {sorted(s.value for s in allowed)})"
            )
        self._states[seat_index] = target

    def is_skippable(self, seat_index: int) -> bool:
        """OCR-1 全局轮询应跳过此 seat 吗?"""
        return self._states[seat_index] in _SKIPPABLE

    def skippable_seats(self) -> set[int]:
        return {i for i, s in self._states.items() if s in _SKIPPABLE}

    def active_seats(self) -> set[int]:
        return {i for i, s in self._states.items() if s == SeatLifecycle.ACTIVE}

    def reset_for_new_hand(self) -> None:
        """At hand boundary, FOLDED / ALL_IN seats may re-activate.

        EMPTY / SITTING_OUT / LEAVING stay as-is (driven by external signals).
        """
        for i, s in list(self._states.items()):
            if s in (SeatLifecycle.FOLDED, SeatLifecycle.ALL_IN):
                # Re-activate; if player went bust, external signal will move to EMPTY.
                self._states[i] = SeatLifecycle.ACTIVE

    def snapshot(self) -> dict[int, SeatLifecycle]:
        return dict(self._states)
