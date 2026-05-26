"""Main capture → recognize → store pipeline."""

import logging
import time
from datetime import datetime, timezone

from capture.roi import ROIManager
from capture.screen import ScreenCapturer
from config import CAPTURE_INTERVAL_MS, ROI_CONFIG_DIR, ROI_PROFILE
from difflib import get_close_matches

import cv2
import numpy as np

from events.models import ActionType, Position
from events.normalizer import compute_confidence, infer_action_from_delta

# Allowlist for action_area OCR — restricts charset to known action keywords + amounts.
# Filters out garbage like "疯鱼罩轩 2"(player name bleed)or random Chinese characters.
ACTION_OCR_ALLOWLIST = (
    "跟加注弃牌过让看盖下全押压前"               # Chinese action keywords
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"                  # English (parser uppers text first)
    "0123456789"                                  # digit amounts
    "-., $"                                       # common separators
)


def _avg_hash_64(bgr_img: np.ndarray) -> str:
    """Simple 64-bit average hash (avg-hash / aHash) of an image region.

    Used as a player avatar fingerprint (#4): same avatar pixels → same hash
    → same identity, regardless of OCR character drift on the nickname.

    Returns 64-char "0"/"1" string. Hamming distance ≤ ~10 considered "same".
    """
    if bgr_img is None or bgr_img.size == 0:
        return ""
    if bgr_img.shape[2] == 4:
        bgr_img = cv2.cvtColor(bgr_img, cv2.COLOR_BGRA2BGR)
    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
    thumb = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
    avg = thumb.mean()
    bits = (thumb > avg).astype(int).flatten()
    return "".join(str(b) for b in bits)


def _hamming(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 999
    return sum(c1 != c2 for c1, c2 in zip(a, b))
from pipeline.detector import StateTracker
from recognition.actions import ActionRecognizer
from recognition.cards import CardRecognizer
from recognition.ocr import OCREngine
from storage.database import SessionLocal
from storage.repository import ActionEventRepository, HandRepository

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Main loop: capture ROIs → recognize cards/actions → persist to DB."""

    def __init__(self, roi_profile: str | None = None):
        profile = roi_profile or ROI_PROFILE
        roi_path = f"{ROI_CONFIG_DIR}/{profile}.json"

        self.roi_manager = ROIManager.from_json(roi_path)
        logger.info(f"Loaded ROI config: {roi_path}")

        self.capturer = ScreenCapturer()

        # Try to find the poker client window from saved config
        window_title = self.roi_manager.rois._window_title if hasattr(self.roi_manager.rois, '_window_title') else ""
        if not window_title:
            # Fallback: check the JSON data
            import json
            with open(roi_path) as f:
                roi_data = json.load(f)
            window_title = roi_data.get("window_title", "")

        if window_title:
            if self.capturer.find_window_by_title(window_title):
                logger.info(f"Tracking window: {window_title!r}")
            else:
                logger.warning(f"Window {window_title!r} not found, falling back to monitor 1")
                self.capturer.select_monitor(1)
        else:
            self.capturer.select_monitor(1)

        self.card_recognizer = CardRecognizer()
        self.action_recognizer = ActionRecognizer()
        self.ocr = OCREngine()

        self.tracker = StateTracker()

        self.hand_repo = HandRepository()
        self.event_repo = ActionEventRepository()

        # Auto-detect PG availability at startup; falls back to "no-db" mode
        # if unreachable (lets smoke testing proceed without forcing PG setup).
        self._db_enabled = self._probe_db()
        if not self._db_enabled:
            logger.warning(
                "PostgreSQL unreachable — running in NO-DB mode. "
                "Pipeline will identify cards/actions but **not persist** to DB. "
                "Configure PG (see docs/dev-workflow.md) when ready for real data."
            )

        self.running = False

    def _probe_db(self) -> bool:
        """Test DB engine can connect; return False on any failure."""
        try:
            from storage.database import engine
            with engine.connect():
                pass
            return True
        except Exception as exc:
            logger.debug(f"DB probe failed: {type(exc).__name__}")
            return False

    def start(self):
        """Run the main capture loop."""
        self.running = True
        logger.info("Pipeline started — capturing every %dms", CAPTURE_INTERVAL_MS)

        try:
            while self.running:
                self._tick()
                time.sleep(CAPTURE_INTERVAL_MS / 1000.0)
        except KeyboardInterrupt:
            logger.info("Pipeline stopped by user")
        finally:
            self._shutdown()

    def stop(self):
        self.running = False

    # ── Tick ──────────────────────────────────────────────

    def _tick(self):
        rois = self.roi_manager.rois
        db = SessionLocal() if self._db_enabled else None

        try:
            # 1. Capture hero cards
            hero_1 = self.capturer.capture_roi(rois.hero_card_1)
            hero_2 = self.capturer.capture_roi(rois.hero_card_2)

            # 2. Detect new hand
            if self.tracker.has_active_hand:
                if self.tracker.check_hero_cards(hero_1, hero_2):
                    self._end_current_hand(db)
                    self._start_new_hand(db, hero_1, hero_2)
            else:
                if self._hero_cards_present(hero_1, hero_2):
                    self._start_new_hand(db, hero_1, hero_2)

            # 3. Community cards
            if self.tracker.has_active_hand:
                self._process_community_cards(db, rois)

            # 3b. Observer-mode hand-start fallback: if hero cards are not
            # available (default ROI = stable browser chrome, never changes),
            # use community count drop from > 0 to 0 as the new-hand signal.
            if self.tracker.has_active_hand and self.tracker.community_just_reset():
                logger.info("Community reset detected → starting new hand (observer mode)")
                self._end_current_hand(db)
                self._start_new_hand(db, hero_1, hero_2)

            # 4. Pot size — runs BEFORE seat actions so that _process_seat_actions
            # has access to both pot_before (saved on tracker._pot_before_tick)
            # and pot_after (= tracker.latest_pot_bb) for cross-validation raw_data.
            if self.tracker.has_active_hand:
                self._process_pot(db, rois)

            # 5. Seat actions — writes raw_data with stack_delta + pot_delta evidence
            if self.tracker.has_active_hand:
                self._process_seat_actions(db, rois)

            if db is not None:
                db.commit()
        except Exception:
            logger.error("Tick failed", exc_info=True)
            if db is not None:
                db.rollback()
        finally:
            if db is not None:
                db.close()

    # ── Hand lifecycle ────────────────────────────────────

    def _hero_cards_present(self, hero_1, hero_2) -> bool:
        """Quick heuristic: are hero cards visible (non-blank)? """
        if hero_1.size == 0 or hero_2.size == 0:
            return False
        # Check if the ROI is mostly uniform (blank card area)
        std1 = hero_1.std() if hero_1.size > 0 else 0
        std2 = hero_2.std() if hero_2.size > 0 else 0
        # Visible cards have variation; blank areas are uniform
        return std1 > 30 and std2 > 30

    @staticmethod
    def _slot_has_card(img) -> bool:
        """Detect whether an ROI looks like a card slot with a card present.

        Card-present ROIs have a white-ish background (mean luminance ≳ 150);
        empty slots show table felt (green/blue, mean ≲ 100). Avoids feeding
        garbage pixels to the CNN classifier which would produce random
        misclassifications (seen as repeated wrong cards in observer mode
        when fewer than 5 community cards are dealt).
        """
        if img is None or img.size == 0:
            return False
        # mss returns BGRA; take BGR for luminance
        bgr = img[..., :3] if img.ndim == 3 and img.shape[2] >= 3 else img
        mean_lum = float(bgr.mean())
        return mean_lum > 150.0

    def _start_new_hand(self, db, hero_1, hero_2):
        hand = self.tracker.start_new_hand()
        c1 = self.card_recognizer.recognize_single(hero_1)
        c2 = self.card_recognizer.recognize_single(hero_2)
        hand.hero_cards = []
        if c1:
            hand.hero_cards.append(f"{c1['rank']}{c1['suit']}")
        if c2:
            hand.hero_cards.append(f"{c2['rank']}{c2['suit']}")

        # DB insert FIRST so that any subsequent error in position/id detection doesn't
        # leave action_events pointing to a non-existent hand_id (FK violation).
        # Position mapping is metadata that can be patched in via update; the FK is not.
        if db is not None:
            try:
                self.hand_repo.create(db, hand)
            except Exception:
                logger.exception(f"Hand {hand.id} DB insert failed; aborting hand")
                self.tracker.current_hand = None
                return

        # Detect button position and compute seat→position mapping
        self._detect_button_position()

        # Capture each seat's platform user-ID before any in-hand action obscures it
        self._capture_player_ids()

        # 12a-pre: snapshot per-seat stack at hand start (before any betting).
        # Stored on hand.raw_data so insurance / rake / stack-delta validation can
        # back-compute on a hand basis later.
        initial_stacks = self._capture_seat_stacks()

        seats_map = {}
        for k, v in self.tracker._position_map.items():
            try:
                seats_map[Position(v)] = self.tracker.player_id_map.get(k, f"Player_{k}")
            except ValueError:
                # Position string not in enum (e.g. fallback "S0" when button unknown);
                # skip this seat from hand.seats — pipeline still tracks via tracker
                logger.debug(f"Skipping seat {k}: position '{v}' not in Position enum")
        hand.seats = seats_map

        if hand.raw_data is None:
            hand.raw_data = {}
        hand.raw_data["player_stacks_initial"] = initial_stacks

        if db is not None and (seats_map or initial_stacks):
            # Update DB hand with seats metadata + initial stacks (best-effort)
            try:
                self.hand_repo.update(db, hand)
            except Exception:
                logger.warning(f"Hand {hand.id} hand-start metadata update failed", exc_info=True)
        logger.info(f"Hand {hand.id} — hero: {hand.hero_cards} — ids: {self.tracker.player_id_map}")

    def _capture_player_ids(self):
        """OCR each seat's id_area at hand-start; record into tracker.player_id_map.

        Hand-start is the only window where IDs are unobstructed: no action text
        ('CALL' / 'RAISE' / etc.) covers them and no fold-grey state degrades them.

        #2 Cache lock: player_id_map persists across hands (not reset by start_new_hand).
                       Already-cached seats are skipped, preventing OCR drift between
                       hands from re-writing the player's name as a variant.

        #3 Fuzzy match: if a new OCR text is within Levenshtein distance ~1 of an
                        existing known player name (any seat), it's treated as an
                        OCR variant of that same player (e.g. "覃" mis-read as "罩")
                        and canonicalized to the existing name.

        Filter: WePoker shows ID and action text at the SAME pixel zone. At hand-start
        transition, the previous hand's action keyword may still be on screen. If
        OCR'd text parses as an ActionType ("跟注"/...) treat as OCR failure.
        """
        for seat in self.roi_manager.rois.seat_regions:
            if seat.id_area is None or seat.id_area.width == 0:
                continue
            # #2 Cache lock: don't re-OCR a seat we already have a valid name for
            if seat.seat_index in self.tracker.player_id_map:
                continue

            # #4 Avatar image fingerprint — try BEFORE OCR. If we've seen this avatar
            # before (anywhere), we already know the player name. fold_area is used as
            # the avatar source since it covers head-center pixels.
            avatar_hash = ""
            if seat.fold_area is not None and seat.fold_area.width > 0:
                avatar_img = self.capturer.capture_roi(seat.fold_area)
                avatar_hash = _avg_hash_64(avatar_img)
                # Match against registry — nearest neighbour by Hamming distance
                best_match = None
                best_dist = 999
                for h, name in self.tracker._avatar_fingerprints.items():
                    d = _hamming(avatar_hash, h)
                    if d < best_dist:
                        best_dist = d
                        best_match = name
                if best_match and best_dist <= 6:  # ~9% bit-diff threshold
                    logger.debug(f"_capture_player_ids: seat_{seat.seat_index} avatar match "
                                 f"{best_match!r} (hamming={best_dist})")
                    self.tracker.player_id_map[seat.seat_index] = best_match
                    continue

            # Fallback to OCR if no fingerprint match
            img = self.capturer.capture_roi(seat.id_area)
            text = self.ocr.read_text(img).strip()
            if not text:
                continue
            # Filter: if text parses as action keyword, it's transition-frame
            # contamination, not a real player nickname.
            if self.action_recognizer.parse(text) is not None:
                logger.debug(f"_capture_player_ids: seat_{seat.seat_index} got action-text "
                             f"{text!r}, skipping (likely transition frame)")
                continue
            # #3 Fuzzy match against names already in the registry (any seat).
            # cutoff=0.75 chosen so 4-char names with 1-char OCR drift match (ratio
            # 0.75 exactly) while 7-char names with 2-char drift don't (ratio 0.71).
            known = list(self.tracker.player_id_map.values())
            if known and len(text) >= 3:
                matches = get_close_matches(text, known, n=1, cutoff=0.75)
                if matches:
                    if matches[0] != text:
                        logger.info(f"_capture_player_ids: seat_{seat.seat_index} OCR'd "
                                    f"{text!r} → canonicalized to {matches[0]!r} (alias)")
                    text = matches[0]
            self.tracker.player_id_map[seat.seat_index] = text
            # #4 Register avatar fingerprint for future lookup
            if avatar_hash:
                self.tracker._avatar_fingerprints[avatar_hash] = text

    def _end_current_hand(self, db):
        # 12a-pre: snapshot per-seat stack BEFORE finalize, while tracker.current_hand
        # is still valid. Stored on hand.raw_data for insurance / rake validation.
        final_stacks = self._capture_seat_stacks()
        cur = self.tracker.current_hand
        if cur is not None:
            if cur.raw_data is None:
                cur.raw_data = {}
            cur.raw_data["player_stacks_final"] = final_stacks

        hand = self.tracker.finalize_hand()
        if hand and db is not None:
            self.hand_repo.update(db, hand)

    def _capture_seat_stacks(self) -> dict[int, float]:
        """Snapshot per-seat stack via OCR (digit-only allowlist).

        Used at hand-start (initial_stacks) and hand-end (final_stacks) to feed
        downstream hand-level validation: rake reverse-compute, insurance
        inference (round-rebuy vs random-payout), per-hand stack conservation.

        Cost ≈ 10-30ms × N seats; called twice per hand (~once per ~30s), light load.
        """
        stacks: dict[int, float] = {}
        for seat in self.roi_manager.rois.seat_regions:
            if seat.stack_area is None or seat.stack_area.width == 0:
                continue
            img = self.capturer.capture_roi(seat.stack_area)
            text = self.ocr.read_text(img, allowlist="0123456789.")
            amount = ActionRecognizer._extract_amount(text)
            if amount is not None:
                stacks[seat.seat_index] = amount
        return stacks

    # ── Community cards ───────────────────────────────────

    def _process_community_cards(self, db, rois):
        texts = []
        all_cards = []
        for cc_roi in rois.community_cards:
            img = self.capturer.capture_roi(cc_roi)
            if not self._slot_has_card(img):
                # Empty community slot (street hasn't dealt this card yet);
                # don't feed garbage pixels to CNN — would predict random card.
                texts.append("")
                continue
            result = self.card_recognizer.recognize_single(img)
            if result:
                texts.append(f"{result['rank']}{result['suit']}")
                all_cards.append(f"{result['rank']}{result['suit']}")
            else:
                texts.append("")

        if self.tracker.check_community_change(texts):
            hand = self.tracker.current_hand
            street = self.tracker.normalizer._current_street
            if street.value not in hand.community_cards:
                hand.community_cards[street] = all_cards
                if db is not None:
                    self.hand_repo.update(db, hand)
                # Suppress "Street preflop: []" — that's redundant with the
                # "Community reset detected" log right after.
                if all_cards:
                    logger.info(f"Street {street.value}: {all_cards}")

    # ── Seat actions ──────────────────────────────────────

    def _process_seat_actions(self, db, rois):
        # NB: iterate using seat_roi.seat_index (NOT enumerate's i) for all
        # tracker state lookups — list-position differs from physical seat_index
        # when only some seats are configured (e.g. partial stage-B setup).
        for seat_roi in rois.seat_regions:
            sidx = seat_roi.seat_index

            # P1 cross-validation: always read stack every tick (not just on action change)
            # so we have stack_before/stack_after on the action that DOES change.
            stack_now = None
            if seat_roi.stack_area is not None and seat_roi.stack_area.width > 0:
                stack_img = self.capturer.capture_roi(seat_roi.stack_area)
                stack_text = self.ocr.read_text(stack_img, allowlist="0123456789.")
                stack_now = ActionRecognizer._extract_amount(stack_text)

            # Priority: check fold_area first — WePoker shows both "弃牌" (fold) AND
            # "ALL IN" (all-in) overlaid on the avatar center. Use the parser to detect
            # whether OCR'd text is a definitive avatar-overlay action (FOLD/ALL_IN);
            # if so, short-circuit the action_area read for this tick.
            action_text = None
            if seat_roi.fold_area is not None:
                fold_img = self.capturer.capture_roi(seat_roi.fold_area)
                fold_text = self.ocr.read_text(fold_img)
                if fold_text:
                    parsed_fold = self.action_recognizer.parse(fold_text)
                    if parsed_fold and parsed_fold["action_type"] in (ActionType.FOLD, ActionType.ALL_IN):
                        action_text = fold_text

            if action_text is None:
                action_img = self.capturer.capture_roi(seat_roi.action_area)
                # #1 OCR allowlist: restrict to known action chars to suppress noise
                # like player-name bleed or random Chinese text. Allowlist is wide
                # enough for all parser-supported keywords + amounts.
                action_text = self.ocr.read_text(action_img, allowlist=ACTION_OCR_ALLOWLIST)

                # Concatenate amount (separate ROI in WePoker — chip-icon + digits beside avatar);
                # parser regex (\d+\.?\d*) will pull the number from the combined text
                if action_text and seat_roi.amount_area is not None:
                    amount_img = self.capturer.capture_roi(seat_roi.amount_area)
                    amount_text = self.ocr.read_text(amount_img, allowlist="0123456789.")
                    if amount_text:
                        action_text = f"{action_text} {amount_text}"

            if not action_text:
                # No event this tick — still update _prev_stack so the NEXT event has
                # an accurate stack_before reading from the same baseline.
                if stack_now is not None:
                    self.tracker._prev_stack[sidx] = stack_now
                continue

            if self.tracker.check_action_change(sidx, action_text):
                parsed = self.action_recognizer.parse(action_text)
                # Diagnostic: log every state-changed action OCR result, even when
                # parser fails. Critical for raise-detection diagnosis.
                parsed_label = parsed["action_type"].value if parsed else "UNPARSED"
                logger.info(f"[OCR seat_{sidx}] text={action_text!r} -> {parsed_label}")
                if parsed is None:
                    continue

                position_str = self.tracker.get_position(sidx)
                try:
                    position = Position(position_str)
                except ValueError:
                    position = Position.UTG  # fallback

                # Detect facing action from previous actions in this hand
                facing = self._build_facing_action(sidx)

                player_name = self.tracker.player_id_map.get(sidx, f"Player_{sidx}")
                event = self.tracker.normalizer.create_event(
                    hand=self.tracker.current_hand,
                    player_name=player_name,
                    position=position,
                    action_type=parsed["action_type"],
                    amount=parsed.get("amount"),
                    facing_action=facing,
                )

                # Attach stack and pot context (stack_now already captured at top of loop)
                event.effective_stack_bb = stack_now
                if self.tracker.latest_pot_bb is not None:
                    event.pot_size_bb = self.tracker.latest_pot_bb

                # P1 cross-validation: record all signals as evidence in raw_data
                # so future layers (P2 equation check, P3 stack-derived inference,
                # P4 review) can reason about them without re-OCR.
                stack_before = self.tracker._prev_stack.get(sidx)
                stack_after = stack_now
                stack_delta = (stack_before - stack_after) if (stack_before is not None and stack_after is not None) else None
                pot_before = self.tracker._pot_before_tick
                pot_after = self.tracker.latest_pot_bb
                pot_delta = (pot_after - pot_before) if (pot_after is not None and pot_before is not None) else None
                # P3 Layer 2: infer action from numerical evidence + poker rules
                stack_derived = infer_action_from_delta(
                    stack_delta,
                    self.tracker._street_to_call,
                    not self.tracker._street_has_bet,
                    stack_after,  # full_stack approx (post-action stack as floor)
                )
                text_derived = parsed["action_type"]
                final_action = text_derived
                override_reason = None
                # Override only when stack-derived is unambiguous AND disagrees with text
                # (REQ Q4=A: stack 优先, confidence 降)
                if stack_derived is not None and stack_derived != text_derived:
                    final_action = stack_derived
                    override_reason = f"stack-derived {stack_derived.value} overrode text-derived {text_derived.value}"
                    logger.info(f"[P3 override] seat_{sidx} text={action_text!r} "
                                f"text→{text_derived.value} stack→{stack_derived.value}")

                event.action_type = final_action  # may be overridden
                event.raw_data = {
                    "action_text": action_text,
                    "stack_before": stack_before,
                    "stack_after": stack_after,
                    "stack_delta": stack_delta,
                    "pot_before": pot_before,
                    "pot_after": pot_after,
                    "pot_delta": pot_delta,
                    "text_derived_action": text_derived.value,
                    "stack_derived_action": stack_derived.value if stack_derived else None,
                    "current_to_call": self.tracker._street_to_call,
                    "is_first_bet_this_street": not self.tracker._street_has_bet,
                    "override_reason": override_reason,
                }
                # P2 Layer 1: physics equation check → confidence_score
                event.confidence_score = compute_confidence(
                    final_action, stack_delta, pot_delta,
                )
                # If P3 overrode, lower confidence (REQ Q4=A 数字优先 + confidence 降)
                if override_reason:
                    event.confidence_score = min(event.confidence_score, 0.5)

                # P3 state: update street tracking after this event
                if stack_delta is not None and stack_delta > 2:
                    self.tracker._street_to_call = max(
                        self.tracker._street_to_call, stack_delta
                    )
                    self.tracker._street_has_bet = True

                if db is not None:
                    self.event_repo.create(db, event)
                logger.info(
                    f"Action: {event.player_name}({position.value}) "
                    f"{event.action_type.value} {event.amount or ''} [{event.street.value}]"
                )

            # Update _prev_stack for NEXT tick's cross-validation baseline (whether or
            # not an event fired this tick — keep the latest reading current).
            if stack_now is not None:
                self.tracker._prev_stack[sidx] = stack_now

    def _process_pot(self, db, rois):
        """Read pot size from ROI and update tracker state.

        Side effects (used by _process_seat_actions downstream for cross-validation):
          - tracker._pot_before_tick = pot value before this tick's update
          - tracker.latest_pot_bb = pot value after this tick's OCR
          - tracker._hand_pot_peak = max over the hand (immune to new-hand transient)
        """
        # Snapshot BEFORE updating so seat-action raw_data can compute pot_delta
        self.tracker._pot_before_tick = self.tracker.latest_pot_bb

        pot_img = self.capturer.capture_roi(rois.pot_size)
        pot_text = self.ocr.read_text(pot_img)
        amount = ActionRecognizer._extract_amount(pot_text)
        if amount is not None and amount != self.tracker.latest_pot_bb:
            logger.info(f"Pot: {amount} (was {self.tracker.latest_pot_bb})")
            self.tracker.latest_pot_bb = amount
        # Update peak independently of whether amount changed
        if amount is not None:
            if self.tracker._hand_pot_peak is None or amount > self.tracker._hand_pot_peak:
                self.tracker._hand_pot_peak = amount

    # ── Helpers ───────────────────────────────────────────

    def _detect_button_position(self):
        """Scan each seat's button_indicator ROI and OCR for the 'D' dealer marker.

        WePoker shows a small "D" tag immediately left of the dealer's chip count.
        The earlier brightness-heuristic was too noisy for such small icons;
        OCR with allowlist='D' gives a definitive signal even at low resolution.
        """
        button_seat = None
        for seat_roi in self.roi_manager.rois.seat_regions:
            if seat_roi.button_indicator is None:
                continue
            img = self.capturer.capture_roi(seat_roi.button_indicator)
            if img.size == 0:
                continue
            text = self.ocr.read_text(img, allowlist="D")
            if "D" in text.upper():
                button_seat = seat_roi.seat_index
                break

        if button_seat is not None:
            self.roi_manager.button_seat_index = button_seat
            logger.info(f"Button detected at seat {button_seat} (OCR)")
        else:
            logger.warning("No button detected via OCR, using seat 0 as default")
            self.roi_manager.button_seat_index = 0

        mapping = self.roi_manager.compute_positions()
        self.tracker.set_position_map(mapping)

    def _build_facing_action(self, seat_idx: int) -> str | None:
        """Describe what action this player is facing."""
        if not self.tracker.has_active_hand:
            return None
        # The last action by any other player is what this player faces
        # Simplified: return last street + last action type
        return None

    def _shutdown(self):
        db = SessionLocal() if self._db_enabled else None
        try:
            if self.tracker.has_active_hand:
                self._end_current_hand(db)
                if db is not None:
                    db.commit()
        finally:
            if db is not None:
                db.close()
        logger.info("Pipeline shutdown complete")
