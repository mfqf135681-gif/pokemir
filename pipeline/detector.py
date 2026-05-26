"""Game state tracker — detects new hands, action changes, street progression."""

import logging
from collections import deque
from datetime import datetime, timezone

import imagehash
import numpy as np
from PIL import Image

from events.models import Hand, Street
from events.normalizer import EventNormalizer

logger = logging.getLogger(__name__)


class StateTracker:
    """Tracks poker game state across capture cycles.

    Detects:
    - New hand start (hero cards change)
    - Action occurrences (seat action text changes)
    - Street advance (community card count changes)
    """

    def __init__(self, hash_threshold: int = 10):
        self.current_hand: Hand | None = None
        self.normalizer = EventNormalizer()
        self._hash_threshold = hash_threshold

        # Previous state for change detection
        self._prev_hero_hash: str | None = None
        self._prev_action_texts: dict[int, str] = {}
        self._prev_community_count: int = 0
        self._position_map: dict[int, str] = {}

        # Latest pot size (from _process_pot), attached to subsequent action events
        self.latest_pot_bb: float | None = None
        # Peak pot size seen during this hand (immune to new-hand reset transients);
        # used by finalize_hand for hands.pot_size_final to avoid the "OCR caught a
        # tiny value at hand-end transition" bug
        self._hand_pot_peak: float | None = None
        # P1 cross-validation: pot value BEFORE this tick's _process_pot ran.
        # Used as pot_before in per-event raw_data evidence (pot_delta = after - before).
        self._pot_before_tick: float | None = None
        # P1 cross-validation: per-seat last stack OCR reading.
        # Used as stack_before in per-event raw_data evidence (stack_delta = before - after).
        self._prev_stack: dict[int, float] = {}
        # P3 Layer 2 inputs: track per-street state for action_type inference
        self._street_to_call: float = 0.0   # max chip contribution by any seat this street
        self._street_has_bet: bool = False  # True after first chip-contributing action this street
        # #4 Avatar image fingerprint registry — maps phash → canonical player name.
        # Persistent across hands (not reset by start_new_hand), forms the foundation
        # of "same avatar = same player" identity regardless of OCR character drift.
        self._avatar_fingerprints: dict[str, str] = {}
        # #12a Per-hand "went all-in" flag — set when any seat's stack hits ~0 mid-hand.
        # Used at finalize_hand to infer insurance buy/payout from stack pattern.
        self._went_all_in_this_hand: set[int] = set()
        # Timer (decision-time countdown) tracking — fold_area shows digit countdown
        # for the seat currently acting. _timer_state maps sidx → (last_seen_countdown,
        # started_at_wall_clock). On disappearance, decision_time_ms is recorded into
        # _pending_decision_time for attribution to the next event from that seat.
        self._timer_state: dict[int, tuple[int, float]] = {}
        self._pending_decision_time: dict[int, int] = {}  # ms
        self._used_timebank: dict[int, bool] = {}
        # Folded seats this hand (set when FOLD event fires) — used to skip them
        # during showdown card detection.
        self._folded_seats: set[int] = set()
        # Seats that fired ANY event this hand (active set). Used at showdown:
        # non_folded_active = _seats_with_events - _folded_seats;
        # if < 2 → no real showdown (single winner / fold-around) → skip CNN.
        self._seats_with_events_this_hand: set[int] = set()
        # Per-seat idle avatar hash (baseline). Captured when fold_area is empty
        # (no overlay text/timer/弃牌/all-in). Persistent across hands (not reset
        # by start_new_hand) — same player at same seat keeps baseline. Used at
        # showdown to detect avatar diverge: if hash ≈ baseline, no overlay
        # appeared on this seat → skip CNN (root-cause fix for seat_X hallucinations).
        self._idle_avatar_hash: dict[int, str] = {}
        # Per-seat 最近 5 次 showdown 预测历史 (anti-hallucination)。
        # 若同一 (card1, card2) tuple 在最近 5 次出现 ≥ 3 次 → 视为 CNN 对该 seat
        # 头像的稳定幻觉 (如 seat_X 永远 3s,3s) → 抑制。
        # 持久跨手,不在 start_new_hand 重置。
        self._seat_pred_history: dict[int, deque] = {}

        # Per-hand seat_index → platform user-ID (OCR'd at hand-start; used as player_name for cross-hand stats)
        self.player_id_map: dict[int, str] = {}

    @property
    def has_active_hand(self) -> bool:
        return self.current_hand is not None

    # ── Hero card detection ───────────────────────────────

    def check_hero_cards(self, hero_1: np.ndarray, hero_2: np.ndarray) -> bool:
        """Return True if hero cards changed (new hand started)."""
        if hero_1.size == 0 or hero_2.size == 0:
            return False

        h1 = self._hash_image(hero_1)
        h2 = self._hash_image(hero_2)
        combined = f"{h1}:{h2}"

        if self._prev_hero_hash is None:
            self._prev_hero_hash = combined
            return False

        # Hash change → new hand
        if combined != self._prev_hero_hash:
            dist1 = h1 - imagehash.hex_to_hash(self._prev_hero_hash.split(":")[0]) if h1 else 0
            dist2 = h2 - imagehash.hex_to_hash(self._prev_hero_hash.split(":")[1]) if h2 else 0
            if dist1 > self._hash_threshold or dist2 > self._hash_threshold:
                self._prev_hero_hash = combined
                return True

        return False

    def _hash_image(self, img: np.ndarray) -> imagehash.ImageHash | None:
        """Compute perceptual hash of an image crop."""
        try:
            if img.shape[2] == 4:
                img = img[..., :3]  # drop alpha
            pil = Image.fromarray(img)
            return imagehash.average_hash(pil)
        except Exception:
            return None

    # ── Action detection ──────────────────────────────────

    def check_action_change(self, seat_idx: int, action_text: str) -> bool:
        """Return True if this seat's action text changed."""
        prev = self._prev_action_texts.get(seat_idx, "")
        cleaned = action_text.strip()
        if cleaned and cleaned != prev:
            self._prev_action_texts[seat_idx] = cleaned
            return True
        return False

    # ── Community card detection ──────────────────────────

    # Canonical poker community counts: 0=preflop, 3=flop, 4=turn, 5=river.
    # Counts 1 and 2 only occur as flop-dealing-animation artifacts and
    # are filtered out of street-change logs (still tracked internally).
    _CANONICAL_COUNTS = frozenset({0, 3, 4, 5})

    def check_community_change(self, card_texts: list[str]) -> bool:
        """Return True if community cards changed to a canonical street state.

        Internal count is always updated to reflect reality (so the
        community_just_reset signal works), but the public return value
        only fires on transitions into {0, 3, 4, 5} — avoiding misleading
        "Street preflop: ['6s']" lines mid-flop-animation.
        """
        count = len([c for c in card_texts if c])
        prev = self._prev_community_count
        if count != prev:
            self._prev_community_count = count
            self._community_just_reset = (prev > 0 and count == 0)
            if count in self._CANONICAL_COUNTS:
                self.normalizer.set_community_card_count(count)
                # P3: street transition → reset per-street betting state
                self._street_to_call = 0.0
                self._street_has_bet = False
                return True
            return False
        self._community_just_reset = False
        return False

    def community_just_reset(self) -> bool:
        """True if community count just dropped to 0 (new hand dealing).

        Used as a fallback hand-start signal in observer mode where hero
        cards are not available to detect hand change via _prev_hero_hash.
        """
        return getattr(self, "_community_just_reset", False)

    # ── Hand lifecycle ────────────────────────────────────

    def start_new_hand(self, table_name: str = "unknown") -> Hand:
        """Create a new Hand object and reset tracking state."""
        self.current_hand = Hand(
            table_name=table_name,
            started_at=datetime.now(timezone.utc),
        )
        self.normalizer.reset()
        self._prev_action_texts.clear()
        self._prev_community_count = 0
        self.latest_pot_bb = None
        self._hand_pot_peak = None
        self._pot_before_tick = None
        self._prev_stack.clear()
        # P3 state: reset street tracking on new hand
        self._street_to_call = 0.0
        self._street_has_bet = False
        # 12a state: reset went-all-in tracking on new hand
        self._went_all_in_this_hand = set()
        # Timer / decision-time / folded-seats: reset per hand
        self._timer_state = {}
        self._pending_decision_time = {}
        self._used_timebank = {}
        self._folded_seats = set()
        self._seats_with_events_this_hand = set()
        # NB: player_id_map NOT reset — #2 cache lock so player IDs persist across
        # hands, preventing OCR drift between hands from creating multiple variants
        # of the same player. Cleared only on pipeline restart.
        logger.info(f"New hand started: {self.current_hand.id}")
        return self.current_hand

    def finalize_hand(self) -> Hand | None:
        """Mark the current hand as ended and return it.

        Uses _hand_pot_peak (max seen during the hand) rather than latest_pot_bb
        because at the moment of community reset → finalize, OCR may catch a
        transient new-hand pot value (e.g. tiny blinds) and corrupt the last reading.
        """
        if self.current_hand is None:
            return None
        self.current_hand.ended_at = datetime.now(timezone.utc)
        self.current_hand.pot_size_final = self._hand_pot_peak
        hand = self.current_hand
        self.current_hand = None
        logger.info(f"Hand ended: {hand.id} (pot peak={hand.pot_size_final})")
        return hand

    # ── Position mapping ──────────────────────────────────

    def set_position_map(self, mapping: dict[int, str]):
        self._position_map = mapping

    def get_position(self, seat_idx: int) -> str:
        return self._position_map.get(seat_idx, f"S{seat_idx}")
