"""Normalize raw recognition results into canonical ActionEvent objects."""

from datetime import datetime, timezone

from events.models import ActionEvent, ActionType, Hand, Position, Street


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
