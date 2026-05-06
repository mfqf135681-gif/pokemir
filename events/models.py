"""Domain models for poker events. Framework-agnostic — no ORM imports here."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Optional
from uuid import UUID, uuid4


class ActionType(StrEnum):
    POST_SB = "post_sb"
    POST_BB = "post_bb"
    POST_ANTE = "post_ante"
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    ALL_IN = "all_in"


class Street(StrEnum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


class Position(StrEnum):
    SB = "SB"
    BB = "BB"
    UTG = "UTG"
    MP = "MP"
    CO = "CO"
    BTN = "BTN"


@dataclass
class ActionEvent:
    """A single action taken by a player at a specific point in a hand."""

    hand_id: UUID
    player_name: str
    position: Position
    street: Street
    action_type: ActionType
    sequence_number: int

    amount: Optional[float] = None          # chips put in on this action
    facing_action: Optional[str] = None      # e.g. "bet 3.5bb" or "raise 12bb"
    effective_stack_bb: Optional[float] = None
    pot_size_bb: Optional[float] = None
    players_in_pot: int = 0

    board_texture: Optional[dict] = None     # {"wet": true, "paired": false, "high_card": "A", ...}
    timestamp: Optional[datetime] = None

    id: UUID = field(default_factory=uuid4)
    raw_data: Optional[dict] = None          # JSONB catch-all


@dataclass
class Hand:
    """A complete poker hand from deal to showdown/forfeit."""

    table_name: str
    game_type: str = "NLH"
    stakes: str = "0.00/0.00"

    hero_name: Optional[str] = None
    hero_position: Optional[Position] = None
    hero_cards: Optional[list[str]] = None       # ["Ah", "Kd"]

    community_cards: dict[Street, list[str]] = field(default_factory=dict)
    seats: dict[Position, str] = field(default_factory=dict)  # position -> player_name

    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    id: UUID = field(default_factory=uuid4)
    result: Optional[dict] = None            # {"win_loss": 15.5, "showdown": true, ...}
    raw_data: Optional[dict] = None
