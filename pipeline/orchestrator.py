"""Main capture → recognize → store pipeline."""

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

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


# T1 Visual debug artifacts — save ROI screenshots for low-confidence events
REVIEW_ARTIFACTS_DIR = Path("data/review")


def _save_review_artifacts(hand_id, sidx, ts_str: str, images: dict, metadata: dict) -> None:
    """Persist captured ROI images + metadata when an event lands at confidence < 0.7.

    Layout: data/review/<hand_id>/seat_<sidx>_<ts>_<kind>.png + meta.json
    User can visually verify the OCR reads against actual screenshots, then
    correct via tools/replay_review.py.
    """
    try:
        hand_dir = REVIEW_ARTIFACTS_DIR / str(hand_id)
        hand_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"seat_{sidx}_{ts_str}"
        for kind, img in images.items():
            if img is None or img.size == 0:
                continue
            cv2.imwrite(str(hand_dir / f"{prefix}_{kind}.png"), img)
        with open(hand_dir / f"{prefix}_meta.json", "w") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        logger.warning("Failed to save review artifacts", exc_info=True)


def _is_round_rebuy(amount: float) -> bool:
    """Heuristic: is this value a likely rebuy/top-up (round number)?

    WePoker rebuy presets are typically multiples of 50/100 (BB level). Insurance
    payouts are arbitrary (pot − rake − premium), almost never round.

    Returns True if the amount looks like a rebuy.
    """
    if amount <= 0:
        return False
    # Common preset values
    if amount in (50, 100, 150, 200, 300, 500, 800, 1000, 1500, 2000, 3000, 5000,
                  8000, 10000, 20000, 50000, 100000):
        return True
    # General: multiples of 50 with no fractional cents
    if amount == int(amount) and int(amount) % 50 == 0:
        return True
    return False


# #10 Player registry persistence — survives pipeline restart
PLAYER_REGISTRY_PATH = Path("data/player_registry.json")


def _load_player_registry() -> dict:
    if not PLAYER_REGISTRY_PATH.exists():
        return {"fingerprints": {}}
    try:
        with open(PLAYER_REGISTRY_PATH) as f:
            return json.load(f)
    except Exception:
        logger.warning(f"Failed to load {PLAYER_REGISTRY_PATH}, starting fresh", exc_info=True)
        return {"fingerprints": {}}


def _save_player_registry(fingerprints: dict) -> None:
    try:
        PLAYER_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PLAYER_REGISTRY_PATH, "w") as f:
            json.dump({"fingerprints": fingerprints}, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.warning(f"Failed to save {PLAYER_REGISTRY_PATH}", exc_info=True)
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

        # #10 Load persistent player registry (avatar fingerprints) from disk
        registry = _load_player_registry()
        self.tracker._avatar_fingerprints = dict(registry.get("fingerprints", {}))
        if self.tracker._avatar_fingerprints:
            logger.info(f"Loaded player registry: {len(self.tracker._avatar_fingerprints)} fingerprints")

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

            # #4 Capture avatar hash early — used for both fingerprint match AND
            # #7 seat-swap detection (avatar diverged from cached → player changed)
            avatar_hash = ""
            if seat.fold_area is not None and seat.fold_area.width > 0:
                avatar_img = self.capturer.capture_roi(seat.fold_area)
                avatar_hash = _avg_hash_64(avatar_img)

            # #7 Seat-swap detection: if cached player exists but avatar diverged
            # significantly from cached hash → player changed seat, release cache
            if avatar_hash and seat.seat_index in self.tracker.player_id_map:
                cached_name = self.tracker.player_id_map[seat.seat_index]
                cached_hash = next(
                    (h for h, n in self.tracker._avatar_fingerprints.items() if n == cached_name),
                    None,
                )
                if cached_hash and _hamming(avatar_hash, cached_hash) > 12:
                    logger.info(f"_capture_player_ids: seat_{seat.seat_index} avatar swap "
                                f"(hamming {_hamming(avatar_hash, cached_hash)}), unlocking "
                                f"{cached_name!r}")
                    del self.tracker.player_id_map[seat.seat_index]

            # #2 Cache lock: don't re-OCR a seat we already have a valid name for
            if seat.seat_index in self.tracker.player_id_map:
                continue

            # #4 Avatar image fingerprint — try BEFORE OCR. If we've seen this avatar
            # before (anywhere), we already know the player name.
            if avatar_hash:
                best_match = None
                best_dist = 999
                for h, name in self.tracker._avatar_fingerprints.items():
                    d = _hamming(avatar_hash, h)
                    if d < best_dist:
                        best_dist = d
                        best_match = name
                if best_match and best_dist <= 6:
                    logger.debug(f"_capture_player_ids: seat_{seat.seat_index} avatar match "
                                 f"{best_match!r} (hamming={best_dist})")
                    self.tracker.player_id_map[seat.seat_index] = best_match
                    continue

            # #5 ID consensus: 2 OCR passes, take the longer non-empty text (longer
            # = more characters captured = less truncation/mis-read).
            img1 = self.capturer.capture_roi(seat.id_area)
            text1 = self.ocr.read_text(img1).strip()
            img2 = self.capturer.capture_roi(seat.id_area)
            text2 = self.ocr.read_text(img2).strip()
            if text1 == text2:
                text = text1
            else:
                # Pick the longer (or first non-empty)
                text = text1 if len(text1) >= len(text2) else text2
                logger.debug(f"_capture_player_ids: seat_{seat.seat_index} consensus "
                             f"{text1!r}/{text2!r} → {text!r}")
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
            # #3 case-insensitive comparison: zhixingheyi == Zhixingheyi
            known = list(self.tracker.player_id_map.values())
            if known and len(text) >= 3:
                known_lower_map = {v.lower(): v for v in known}
                lower_matches = get_close_matches(text.lower(), list(known_lower_map.keys()),
                                                  n=1, cutoff=0.75)
                if lower_matches:
                    canonical = known_lower_map[lower_matches[0]]
                    if canonical != text:
                        logger.info(f"_capture_player_ids: seat_{seat.seat_index} OCR'd "
                                    f"{text!r} → canonicalized to {canonical!r} (alias)")
                    text = canonical
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
            # #12a Insurance inference from stack pattern (用户假说):
            #   went-all-in players whose stack_final is non-zero AND non-round
            #   = likely bought insurance (payout came back as random number).
            #   Round-number stacks = rebuy; zero = lost without insurance.
            insurance_results = self._infer_insurance(cur, final_stacks)
            if insurance_results:
                cur.raw_data["insurance_inferred"] = insurance_results
            # Showdown card detection: scan non-folded seats' fold_area with CNN.
            # WePoker reveals 2 hole cards at avatar center at showdown.
            showdown_cards = self._capture_showdown_cards()
            if showdown_cards:
                cur.raw_data["showdown_cards"] = showdown_cards

        hand = self.tracker.finalize_hand()
        if hand and db is not None:
            self.hand_repo.update(db, hand)
        # 周期 dedupe player_id_map(catch 后 OCR 出现的变体,如小鬼微熏/徵熏)
        self._canonicalize_player_id_map()
        # #10 Persist player registry at hand end (cheap; small JSON file)
        _save_player_registry(self.tracker._avatar_fingerprints)

    def _infer_insurance(self, hand, final_stacks: dict[int, float]) -> list[dict]:
        """#12a Infer insurance buys from stack patterns (user hypothesis + win/lose split).

        Refined: also uses stack GAIN vs pot to distinguish winners from insurance buyers:
          - gain ≥ pot × 0.5 → likely won main pot (NOT insurance)
          - gain ≈ 0  AND stack_final ≈ 0 → lost without insurance
          - gain ≈ 0  AND stack_final round → rebuy
          - gain < pot × 0.5 AND non-round → INSURANCE PAYOUT (买保险输了)

        Returns list of dicts.
        """
        results = []
        # Bug 2 fix: pot_size_final is set by finalize_hand() which runs AFTER
        # _infer_insurance. Use _hand_pot_peak directly (the eventual pot_size_final).
        pot = self.tracker._hand_pot_peak or hand.pot_size_final or 0
        initial_stacks = (hand.raw_data or {}).get("player_stacks_initial", {})
        for sidx in self.tracker._went_all_in_this_hand:
            sidx_key = str(sidx) if str(sidx) in initial_stacks else sidx
            init_val = initial_stacks.get(sidx_key, initial_stacks.get(sidx, 0))
            final_val = final_stacks.get(sidx, 0)
            try:
                gain = float(final_val) - float(init_val)
            except (TypeError, ValueError):
                gain = 0
            player_name = self.tracker.player_id_map.get(sidx, f"Player_{sidx}")

            # Win detection first — winners have large positive gains relative to pot
            if pot > 0 and gain >= pot * 0.5:
                classification = "won_main_pot"
                results.append({
                    "seat": sidx, "player_name": player_name,
                    "stack_initial": init_val, "stack_final": final_val,
                    "gain": gain, "pot": pot, "classification": classification,
                })
                continue
            if final_val < 1:
                classification = "lost_no_insurance"
            elif _is_round_rebuy(final_val):
                classification = "rebuy"
            elif pot > 0 and 0 < gain < pot * 0.5:
                # Small positive gain + non-round = likely insurance payout
                classification = "insurance_payout"
                rake_est = pot * 0.05
                premium_inferred = max(0, pot - rake_est - gain)
                results.append({
                    "seat": sidx, "player_name": player_name,
                    "stack_initial": init_val, "stack_final": final_val,
                    "gain": gain, "pot": pot,
                    "premium_inferred": premium_inferred,
                    "classification": classification,
                })
                logger.info(f"[12a insurance] seat_{sidx} {player_name!r} likely bought "
                            f"insurance: gain={gain} (pot={pot}, premium~{premium_inferred:.0f})")
                continue
            else:
                classification = "unknown"
            results.append({
                "seat": sidx, "player_name": player_name,
                "stack_initial": init_val, "stack_final": final_val,
                "gain": gain, "pot": pot, "classification": classification,
            })
        return results

    def _canonicalize_player_id_map(self) -> None:
        """周期性 dedupe player_id_map.

        Initial fuzzy match only runs on first registration per seat. If seat_A
        first reads "小鬼徵熏" then seat_B later reads "小鬼微熏", neither will
        trigger merge (cache lock + one-way fuzzy). This sweeps the whole map and
        canonicalizes aliases:
          - Build clusters by fuzzy similarity (cutoff 0.75)
          - Pick LONGEST name in cluster as canonical (more chars = more info)
          - Rewrite all seat → canonical
        """
        names_set = set(self.tracker.player_id_map.values())
        if len(names_set) <= 1:
            return
        names = list(names_set)
        # Build canonical map: alias → canonical (longest in cluster)
        canonical: dict[str, str] = {n: n for n in names}
        for i, n_i in enumerate(names):
            if len(n_i) < 3:
                continue
            for n_j in names[i + 1:]:
                if len(n_j) < 3:
                    continue
                # Case-insensitive fuzzy compare
                matches = get_close_matches(n_i.lower(), [n_j.lower()], n=1, cutoff=0.75)
                if not matches:
                    continue
                # Aliased — pick longer as canonical (or first alphabetically tiebreak)
                pick = n_i if len(n_i) > len(n_j) else (n_j if len(n_j) > len(n_i) else min(n_i, n_j))
                # Apply: rewrite both their canonical entries
                old_can_i = canonical.get(n_i, n_i)
                old_can_j = canonical.get(n_j, n_j)
                # Propagate to all entries that map to either
                for k, v in list(canonical.items()):
                    if v == old_can_i or v == old_can_j:
                        canonical[k] = pick
        # Apply to player_id_map
        for sidx, current in list(self.tracker.player_id_map.items()):
            new = canonical.get(current, current)
            if new != current:
                logger.info(f"_canonicalize: seat_{sidx} {current!r} → {new!r} (alias merged)")
                self.tracker.player_id_map[sidx] = new

    def _process_timer(self, sidx: int, countdown: int) -> None:
        """Timer countdown digit just observed at fold_area. Track per seat:
        first sighting → start clock; subsequent sightings update; if countdown
        increased > 2 since last seen, timebank was used.
        """
        state = self.tracker._timer_state.get(sidx)
        now = time.time()
        if state is None:
            # First appearance of countdown for this seat this hand
            self.tracker._timer_state[sidx] = (countdown, now)
            return
        prev_countdown, started_at = state
        if countdown > prev_countdown + 2:
            # Countdown rebounded upward → timebank consumed
            self.tracker._used_timebank[sidx] = True
        # Keep started_at as the original start (decision_time = total elapsed)
        self.tracker._timer_state[sidx] = (countdown, started_at)

    def _finalize_timer(self, sidx: int) -> None:
        """Timer disappeared (action happened or idle). Idempotent: only fires
        if a timer state existed for this seat. Stores elapsed ms into
        _pending_decision_time for attribution to the NEXT action event.
        """
        state = self.tracker._timer_state.pop(sidx, None)
        if state is None:
            return
        _, started_at = state
        decision_time_ms = int((time.time() - started_at) * 1000)
        self.tracker._pending_decision_time[sidx] = decision_time_ms

    def _capture_showdown_cards(self) -> dict[int, list[str]]:
        """At hand-end, for each NON-folded seat try CNN on cards_area to read
        the 2 revealed hole cards (showdown).

        Bug 1 fix:
        - GATE 1: only run if community has 5 cards (river / showdown stage).
          Pre-river fold-arounds have no showdown possible.
        - GATE 2: per-card CNN confidence threshold (rank_conf > 0.7 AND
          suit_conf > 0.7). Filters out hallucinations from non-card pixels.

        Saved to hand.raw_data['showdown_cards']. Best-effort: silently skip
        on missing ROI / low confidence / single-card detection.
        """
        # Gate 1: only at river / showdown stage
        hand = self.tracker.current_hand
        if hand is None:
            return {}
        from events.models import Street
        community = hand.community_cards.get(Street.RIVER) if hand.community_cards else None
        if not community or len(community) < 5:
            return {}

        # Gate 2: real showdown requires ≥ 2 non-folded ACTIVE seats.
        # Fold-around-on-river (single winner takes pot) = NO showdown.
        active = self.tracker._seats_with_events_this_hand - self.tracker._folded_seats
        if len(active) < 2:
            logger.debug(f"[showdown] skip: only {len(active)} non-folded active seat(s)")
            return {}

        # Gate 3: PRE-FILTER candidate seats by avatar baseline divergence.
        # User insight: 头像区平时稳定;showdown 时 cards overlay 上去 → hash 显著变化.
        # 若当前 fold_area hash ≈ idle_baseline_hash, 头像没真变化 → 没真摊牌 → skip CNN.
        # 这是 root-cause fix for seat_X CNN 幻觉(如 seat_6 头像永久被识别 3s 3s).
        BASELINE_DIVERGE_THRESHOLD = 10  # hamming > 10 of 64 bits = 显著变化
        candidates = []  # [(seat, current_hash, hamming_diff)]
        for seat in self.roi_manager.rois.seat_regions:
            sidx = seat.seat_index
            if sidx in self.tracker._folded_seats:
                continue
            if seat.fold_area is None or seat.fold_area.width == 0:
                continue
            fold_img_now = self.capturer.capture_roi(seat.fold_area)
            if fold_img_now is None or fold_img_now.size == 0:
                continue
            current_hash = _avg_hash_64(fold_img_now)
            baseline = self.tracker._idle_avatar_hash.get(sidx)
            if baseline is None:
                # 没建过 baseline — 保守跳过(宁可漏不假阳)
                logger.debug(f"[showdown] seat_{sidx} skipped: no idle baseline yet")
                continue
            diff = _hamming(current_hash, baseline)
            if diff < BASELINE_DIVERGE_THRESHOLD:
                # 头像跟基线一样 — 没 overlay,跳过
                logger.debug(f"[showdown] seat_{sidx} baseline matches "
                             f"(hamming={diff}<{BASELINE_DIVERGE_THRESHOLD}), no overlay")
                continue
            candidates.append((seat, diff))

        # Gate 4: 多 seat 同步性 — 真摊牌必然 ≥ 2 seat 同时 avatar diverge
        if len(candidates) < 2:
            logger.debug(f"[showdown] skip: only {len(candidates)} seat with avatar diverge "
                         f"(real showdown requires ≥ 2)")
            return {}

        # Gate 5: per-card CNN confidence threshold
        CONF_THRESHOLD = 0.9
        cards_by_seat = {}
        for seat, diff in candidates:
            sidx = seat.seat_index
            if seat.cards_area is None or seat.cards_area.width == 0:
                continue
            img = self.capturer.capture_roi(seat.cards_area)
            if img is None or img.size == 0:
                continue
            h, w = img.shape[:2]
            if w < 40 or h < 40:
                continue
            card_zone = img[: int(h * 0.8), :]
            left_card = self.card_recognizer.recognize_single(card_zone[:, : w // 2])
            right_card = self.card_recognizer.recognize_single(card_zone[:, w // 2 :])
            cards = []
            for c in (left_card, right_card):
                if not c:
                    continue
                rc = c.get("rank_conf", 1.0)
                sc = c.get("suit_conf", 1.0)
                if rc < CONF_THRESHOLD or sc < CONF_THRESHOLD:
                    logger.debug(f"seat_{sidx} skipped low-conf card "
                                 f"{c['rank']}{c['suit']} (rc={rc:.2f}, sc={sc:.2f})")
                    continue
                cards.append(f"{c['rank']}{c['suit']}")
            if len(cards) == 2:
                cards_by_seat[sidx] = cards
                logger.info(f"[showdown] seat_{sidx} cards: {cards} "
                            f"(avatar hamming={diff})")
        return cards_by_seat

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

        # #4 Duplicate-card sanity: poker has no two identical cards in same hand.
        # If CNN mis-classifies a card mid-animation we may see duplicates; reject
        # the update to avoid polluting community_cards JSONB.
        if all_cards and len(set(all_cards)) != len(all_cards):
            logger.warning(f"Duplicate cards detected in community: {all_cards}; skipping update")
            return

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
                # Digit-miss sanity: reject sudden ≥10x jump (OCR misread digits like
                # 3001001 should-be-300100, or 2841 vs 28410). Keep prior reading.
                prev_stack = self.tracker._prev_stack.get(sidx)
                if (stack_now is not None and prev_stack is not None and prev_stack > 0
                        and (stack_now > prev_stack * 9 or stack_now * 9 < prev_stack)):
                    logger.debug(f"seat_{sidx} stack OCR jump {prev_stack}→{stack_now}, "
                                 f"likely digit miss, keeping prev")
                    stack_now = prev_stack

            # fold_area is a MULTI-PURPOSE overlay zone at the avatar center.
            # WePoker shows different things at different game states:
            #   - 1-2 digit countdown → seat is currently acting (decision timer)
            #   - "弃牌" → FOLD action
            #   - "ALL IN" → ALL_IN action
            #   - 2 card images → showdown hole cards reveal (CNN-readable)
            #   - empty → idle
            action_text = None
            action_img = None  # T1: track for later artifact saving
            if seat_roi.fold_area is not None:
                fold_img = self.capturer.capture_roi(seat_roi.fold_area)
                fold_text = self.ocr.read_text(fold_img)
                ft = fold_text.strip() if fold_text else ""
                # Bug 3 fix: regex extracts digits even with surrounding noise
                # (e.g. "15 sec" / "15." / " 15"). Permissive but bounded to
                # 0-60 (reasonable timer range).
                timer_match = re.search(r"\b(\d{1,2})\b", ft) if ft else None
                # Branch 1: digit found and looks like timer → countdown
                if timer_match and 0 <= int(timer_match.group(1)) <= 60:
                    # Also gate: text shouldn't contain action keywords (avoids
                    # "跟注 100" being parsed as timer "100").
                    if self.action_recognizer.parse(ft) is None:
                        self._process_timer(sidx, int(timer_match.group(1)))
                        if stack_now is not None:
                            self.tracker._prev_stack[sidx] = stack_now
                        continue
                # Branch 2: parser hits FOLD / ALL_IN keyword → action via fold_area
                if ft:
                    parsed_fold = self.action_recognizer.parse(ft)
                    if parsed_fold and parsed_fold["action_type"] in (ActionType.FOLD, ActionType.ALL_IN):
                        action_text = ft
                        self._finalize_timer(sidx)  # timer ended via fold/all-in
                # Branch 3: empty (idle / between actions) → finalize timer
                # AND update idle_avatar_hash baseline (this is the stable "no overlay"
                # state for this seat). Used at hand-end to detect真摊牌 vs hallucination.
                else:
                    self._finalize_timer(sidx)
                    if sidx not in self.tracker._folded_seats and fold_img.size > 0:
                        self.tracker._idle_avatar_hash[sidx] = _avg_hash_64(fold_img)

            if action_text is None:
                action_img = self.capturer.capture_roi(seat_roi.action_area)
                # #1 OCR allowlist: restrict to known action chars to suppress noise
                # like player-name bleed or random Chinese text. Allowlist is wide
                # enough for all parser-supported keywords + amounts.
                # #8 ensemble: dual-scale OCR (2x + 3x) for action — improves Chinese
                # accuracy at cost of one extra OCR call per actioning seat.
                action_text = self.ocr.read_text(action_img, allowlist=ACTION_OCR_ALLOWLIST,
                                                 ensemble=True)

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
                # If P3 overrode: set confidence to 0.7 (signaling "auto-corrected,
                # use with caution but not pure low-signal"). Distinguishes from 0.5
                # which means "no signal available to verify".
                if override_reason:
                    event.confidence_score = 0.7

                # P3 state: update street tracking after this event
                if stack_delta is not None and stack_delta > 2:
                    self.tracker._street_to_call = max(
                        self.tracker._street_to_call, stack_delta
                    )
                    self.tracker._street_has_bet = True

                # 12a: mark seat as having gone all-in this hand (for insurance inference)
                if final_action == ActionType.ALL_IN or (stack_after is not None and stack_after <= 5):
                    self.tracker._went_all_in_this_hand.add(sidx)

                # Track folded seats (for showdown CNN skip + insurance defaults)
                if final_action == ActionType.FOLD:
                    self.tracker._folded_seats.add(sidx)
                # Track ALL active seats (had any event this hand) for showdown gate
                self.tracker._seats_with_events_this_hand.add(sidx)

                # Attach decision_time (timer-derived) + timebank flag to event.raw_data.
                # _finalize_timer was called when fold_area returned non-digit/empty —
                # decision_time was stashed in _pending_decision_time keyed by sidx.
                dt_ms = self.tracker._pending_decision_time.pop(sidx, None)
                if dt_ms is not None:
                    event.raw_data["decision_time_ms"] = dt_ms
                if self.tracker._used_timebank.pop(sidx, False):
                    event.raw_data["used_timebank"] = True

                if db is not None:
                    self.event_repo.create(db, event)

                # T1 Visual debug artifacts: low-confidence events get a screenshot
                # dump for human review. User can browse data/review/<hand_id>/ +
                # use tools/replay_review.py to apply corrections.
                if event.confidence_score < 0.7 and self.tracker.current_hand is not None:
                    ts_str = datetime.now(timezone.utc).strftime("%H%M%S")
                    # Re-capture stack img (we already used stack_now; re-capture is cheap)
                    artifacts = {
                        "action": action_img,  # may be None if fold_area path
                        "stack": self.capturer.capture_roi(seat_roi.stack_area) if seat_roi.stack_area else None,
                    }
                    if seat_roi.fold_area is not None:
                        artifacts["fold"] = self.capturer.capture_roi(seat_roi.fold_area)
                    if seat_roi.amount_area is not None:
                        artifacts["amount"] = self.capturer.capture_roi(seat_roi.amount_area)
                    _save_review_artifacts(
                        hand_id=self.tracker.current_hand.id,
                        sidx=sidx,
                        ts_str=ts_str,
                        images=artifacts,
                        metadata={
                            "event_id": str(event.id),
                            "player_name": event.player_name,
                            "position": position.value,
                            "action_type": event.action_type.value,
                            "amount": event.amount,
                            "confidence_score": event.confidence_score,
                            "raw_data": event.raw_data,
                        },
                    )

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

        # T2 pot monotonicity sanity: pot can only INCREASE within a hand (or stay
        # same). A drop > 10% is almost certainly OCR misread (e.g. lost a digit:
        # 1234 → 234). Ignore the bad reading; keep latest_pot_bb stable.
        if (amount is not None
                and self.tracker.latest_pot_bb is not None
                and amount < self.tracker.latest_pot_bb * 0.9):
            logger.warning(f"Pot OCR decrease ignored: "
                           f"{self.tracker.latest_pot_bb} → {amount} (likely OCR error)")
            return

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
