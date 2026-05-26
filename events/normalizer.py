"""Normalize raw recognition results into canonical ActionEvent objects."""

from datetime import datetime, timezone
from typing import Optional

from events.models import ActionEvent, ActionType, Hand, Position, Street


def infer_action_from_delta(
    stack_delta: Optional[float],
    current_to_call: float,
    is_first_bet_this_street: bool,
    full_stack: Optional[float] = None,
) -> Optional[ActionType]:
    """P3 Layer 2: infer action_type from numerical evidence + poker rules.

    Returns None when stack_delta is unknown or ambiguous;
    caller falls back to text-derived action.

    Rules (assuming single-actor tick):
      stack_delta ≈ 0:
        facing bet (to_call > 0) → fold; else → check
      stack_delta ≈ full_stack:
        → all_in
      stack_delta ≈ current_to_call:
        → call
      stack_delta > 0 and first bet this street (to_call == 0):
        → bet
      stack_delta > current_to_call (raise above the to_call):
        → raise
    """
    if stack_delta is None:
        return None
    sd = abs(stack_delta)
    # Zero-contribution actions (allow 2-chip OCR tolerance)
    if sd <= 2:
        if current_to_call > 2:
            return ActionType.FOLD
        return ActionType.CHECK
    # All-in: stack delta covers the whole stack (within tolerance)
    if full_stack is not None and full_stack > 0 and sd >= full_stack - 2:
        return ActionType.ALL_IN
    # Call: matches the current required-to-call amount
    if current_to_call > 0 and abs(sd - current_to_call) <= 2:
        return ActionType.CALL
    # Bet: first chip-contribution on this street (nobody else bet yet)
    if is_first_bet_this_street and current_to_call <= 2:
        return ActionType.BET
    # Raise: contributed more than just calling
    return ActionType.RAISE


def compute_confidence(
    action_type: ActionType,
    stack_delta: Optional[float],
    pot_delta: Optional[float],
) -> float:
    """4-layer cross-validation Layer 1: physics equation check.

    For chip-contributing actions (bet/call/raise/all_in): pot_delta should
    match stack_delta (assuming single-actor tick, the common case).
    For zero-contribution actions (fold/check): stack_delta should be ~0.

    Returns confidence in [0.3, 1.0]. Below 0.7 → review category (REQ阈值).

    P2 simple version (single-actor assumption). Multi-actor ticks will
    naturally score lower; P3 (Layer 2 poker rules) refines further.
    """
    # Fold / check: must have zero stack contribution
    if action_type in (ActionType.FOLD, ActionType.CHECK):
        if stack_delta is None:
            return 0.85  # missing signal but action allows zero, optimistic
        if abs(stack_delta) <= 2:  # OCR noise tolerance
            return 1.0
        # stack changed but action says no contribution → suspicious
        return 0.3

    # Chip-contributing actions (bet/call/raise/all_in/post_sb/post_bb/post_ante)
    if stack_delta is None and pot_delta is None:
        return 0.5  # no numerical verification possible
    if stack_delta is None or pot_delta is None:
        return 0.7  # partial signal

    # Both signals present — compare
    diff = abs(pot_delta - stack_delta)
    if diff <= 2:  # within OCR noise floor
        return 1.0
    base = max(abs(stack_delta), abs(pot_delta), 1.0)
    rel = diff / base
    if rel <= 0.10:
        return 0.9
    if rel <= 0.30:
        return 0.7
    return 0.3


class EventNormalizer:
    """Converts raw recognition output into standardized ActionEvent objects.

    Handles:
    - Position mapping from seat index using button location
    - Street progression tracking (preflop → flop → turn → river)
    - Effective stack estimation
    - Board texture tagging
    """

    _street_order = [Street.PREFLOP, Street.FLOP, Street.TURN, Street.RIVER, Street.SHOWDOWN]

    def __init__(self):
        self._current_street: Street = Street.PREFLOP
        self._sequence: int = 0
        self._community_card_count: int = 0

    def set_community_card_count(self, count: int):
        """Update street based on community card count."""
        if count == 0:
            self._current_street = Street.PREFLOP
        elif count == 3:
            self._current_street = Street.FLOP
        elif count == 4:
            self._current_street = Street.TURN
        elif count >= 5:
            self._current_street = Street.RIVER
        self._community_card_count = count

    def create_event(
        self,
        hand: Hand,
        player_name: str,
        position: Position,
        action_type: ActionType,
        amount: float | None = None,
        facing_action: str | None = None,
        **kwargs,
    ) -> ActionEvent:
        """Create a normalized ActionEvent with auto-incremented sequence."""
        self._sequence += 1

        return ActionEvent(
            hand_id=hand.id,
            player_name=player_name,
            position=position,
            street=self._current_street,
            action_type=action_type,
            sequence_number=self._sequence,
            amount=amount,
            facing_action=facing_action,
            timestamp=datetime.now(timezone.utc),
            **kwargs,
        )

    def reset(self):
        self._current_street = Street.PREFLOP
        self._sequence = 0
        self._community_card_count = 0

    @staticmethod
    def tag_board_texture(community_cards: list[str]) -> dict:
        """Tag board texture from community cards.

        Returns dict with keys like: wet, paired, monotone, high_card, straight_draw, double_suited
        """
        if len(community_cards) < 3:
            return {}

        ranks = [c[0] for c in community_cards]
        suits = [c[1] for c in community_cards]

        rank_counts = {}
        for r in ranks:
            rank_counts[r] = rank_counts.get(r, 0) + 1

        suit_counts = {}
        for s in suits:
            suit_counts[s] = suit_counts.get(s, 0) + 1

        texture = {
            "paired": any(v >= 2 for v in rank_counts.values()),
            "trips": any(v >= 3 for v in rank_counts.values()),
            "monotone": any(v >= 3 for v in suit_counts.values()) if len(community_cards) >= 3 else False,
            "double_suited": sum(1 for v in suit_counts.values() if v >= 2) >= 2,
        }

        high_ranks = {"A", "K", "Q", "J", "T"}
        board_high = [r for r in ranks if r in high_ranks]
        texture["high_card"] = max(board_high, key=lambda r: "AKQJT".index(r)) if board_high else max(ranks)

        texture["wet"] = (
            texture["double_suited"]
            or (not texture["monotone"] and any(v >= 2 for v in suit_counts.values()))
        )

        return texture
