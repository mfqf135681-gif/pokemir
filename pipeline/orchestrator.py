"""Main capture → recognize → store pipeline."""

import logging
import time
from datetime import datetime, timezone

from capture.roi import ROIManager
from capture.screen import ScreenCapturer
from config import CAPTURE_INTERVAL_MS, ROI_CONFIG_DIR, ROI_PROFILE
from events.models import ActionType, Position
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

            # 4. Seat actions
            if self.tracker.has_active_hand:
                self._process_seat_actions(db, rois)

            # 5. Pot size
            if self.tracker.has_active_hand:
                self._process_pot(db, rois)

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

        # Detect button position and compute seat→position mapping
        self._detect_button_position()

        # Capture each seat's platform user-ID before any in-hand action obscures it
        self._capture_player_ids()

        hand.seats = {
            Position(v): self.tracker.player_id_map.get(k, f"Player_{k}")
            for k, v in self.tracker._position_map.items()
        }
        if db is not None:
            self.hand_repo.create(db, hand)
        logger.info(f"Hand {hand.id} — hero: {hand.hero_cards} — ids: {self.tracker.player_id_map}")

    def _capture_player_ids(self):
        """OCR each seat's id_area at hand-start; record into tracker.player_id_map.

        Hand-start is the only window where IDs are unobstructed: no action text
        ('CALL' / 'RAISE' / etc.) covers them and no fold-grey state degrades them.
        """
        id_map: dict[int, str] = {}
        for seat in self.roi_manager.rois.seat_regions:
            if seat.id_area is None or seat.id_area.width == 0:
                continue
            img = self.capturer.capture_roi(seat.id_area)
            text = self.ocr.read_text(img).strip()
            if text:
                id_map[seat.seat_index] = text
        self.tracker.player_id_map = id_map

    def _end_current_hand(self, db):
        hand = self.tracker.finalize_hand()
        if hand and db is not None:
            self.hand_repo.update(db, hand)

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
        for i, seat_roi in enumerate(rois.seat_regions):
            # Priority: check fold_area first — WePoker shows "弃牌" at avatar center
            # (separate pixel zone from action_area which is above the avatar). If a fold
            # is detected here, skip the action_area read for this tick.
            action_text = None
            if seat_roi.fold_area is not None:
                fold_img = self.capturer.capture_roi(seat_roi.fold_area)
                fold_text = self.ocr.read_text(fold_img)
                if fold_text and "弃牌" in fold_text:
                    action_text = fold_text

            if action_text is None:
                action_img = self.capturer.capture_roi(seat_roi.action_area)
                action_text = self.ocr.read_text(action_img)

            if not action_text:
                continue

            if self.tracker.check_action_change(i, action_text):
                parsed = self.action_recognizer.parse(action_text)
                if parsed is None:
                    logger.debug(f"Seat {i} unparsed: {action_text!r}")
                    continue

                position_str = self.tracker.get_position(i)
                try:
                    position = Position(position_str)
                except ValueError:
                    position = Position.UTG  # fallback

                # Detect facing action from previous actions in this hand
                facing = self._build_facing_action(i)

                player_name = self.tracker.player_id_map.get(i, f"Player_{i}")
                event = self.tracker.normalizer.create_event(
                    hand=self.tracker.current_hand,
                    player_name=player_name,
                    position=position,
                    action_type=parsed["action_type"],
                    amount=parsed.get("amount"),
                    facing_action=facing,
                )

                # Attach stack and pot context
                if seat_roi.stack_area.width > 0:
                    stack_img = self.capturer.capture_roi(seat_roi.stack_area)
                    stack_text = self.ocr.read_text(stack_img)
                    event.effective_stack_bb = ActionRecognizer._extract_amount(stack_text)
                if self.tracker.latest_pot_bb is not None:
                    event.pot_size_bb = self.tracker.latest_pot_bb

                if db is not None:
                    self.event_repo.create(db, event)
                logger.info(
                    f"Action: {event.player_name}({position.value}) "
                    f"{event.action_type.value} {event.amount or ''} [{event.street.value}]"
                )

    def _process_pot(self, db, rois):
        """Read pot size from ROI and stash on tracker; next action event will pick it up."""
        pot_img = self.capturer.capture_roi(rois.pot_size)
        pot_text = self.ocr.read_text(pot_img)
        amount = ActionRecognizer._extract_amount(pot_text)
        if amount is not None and amount != self.tracker.latest_pot_bb:
            logger.info(f"Pot: {amount} (was {self.tracker.latest_pot_bb})")
            self.tracker.latest_pot_bb = amount

    # ── Helpers ───────────────────────────────────────────

    def _detect_button_position(self):
        """Scan each seat's button indicator area to find the dealer button."""
        button_seat = None
        for seat_roi in self.roi_manager.rois.seat_regions:
            if seat_roi.button_indicator is None:
                continue
            img = self.capturer.capture_roi(seat_roi.button_indicator)
            # The button indicator is usually a bright "D" or a distinct icon.
            # Simple heuristic: higher brightness variance = button present
            if img.size > 0:
                gray = img[..., :3].mean(axis=2) if img.shape[2] >= 3 else img
                brightness = gray.mean()
                # Button area tends to be brighter than empty seat area
                if brightness > 60:
                    button_seat = seat_roi.seat_index
                    break

        if button_seat is not None:
            self.roi_manager.button_seat_index = button_seat
            logger.info(f"Button detected at physical seat {button_seat}")
        else:
            logger.warning("No button detected, using seat 0 as default")
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
