"""Main capture → recognize → store pipeline."""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

# Training-data harvest: when ON, _capture_showdown_cards saves each candidate
# seat's L/R card half to data/showdown_dumps/<hand_id>/seat_X_<L|R>_HHMMSS.png
# with sibling .json metadata (CNN guess + conf + hamming). Off by env override.
SHOWDOWN_DUMP_ENABLED = os.getenv("POKEMIR_SHOWDOWN_DUMP", "1") != "0"

from capture.roi import ROIManager
from capture.screen import ScreenCapturer
from config import CAPTURE_INTERVAL_MS, ROI_CONFIG_DIR, ROI_PROFILE
from difflib import get_close_matches

import cv2
import numpy as np

from events.models import ActionType, Position
from events.normalizer import compute_confidence, infer_action_from_delta
from events import diag

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
from sqlalchemy import text as sql_text

from storage.database import SessionLocal
from storage.repository import ActionEventRepository, HandRepository

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Main loop: capture ROIs → recognize cards/actions → persist to DB."""

    def __init__(self, roi_profile: str | None = None, observer_mode: bool = False):
        profile = roi_profile or ROI_PROFILE
        roi_path = f"{ROI_CONFIG_DIR}/{profile}.json"

        self.roi_manager = ROIManager.from_json(roi_path)
        logger.info(f"Loaded ROI config: {roi_path}")

        # T11 (2026-05-28):观战模式 — 用户未坐下,seat[hero_seat_idx] 实际是别人。
        # 关闭 hero seat 自动检测,所有 seat 走对手摊牌捕获逻辑。
        self.observer_mode = observer_mode
        if observer_mode:
            logger.info("[observer-mode] 启用:hero seat 自动检测关闭,所有 seat 等同处理")

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

            # 6. Live showdown capture — must happen DURING river phase while overlay
            # is visible.  Old architecture grabbed at hand-end (after community reset)
            # → overlay gone → caught only avatar pixels.  See 2026-05-26 diff diagnosis.
            if self.tracker.has_active_hand:
                self._try_capture_showdown_live(rois)

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

        # 摊牌 baseline 强初始化 — 之前 baseline 只在 fold_area OCR 为空时更新,
        # 但高活跃玩家很少有 idle empty tick → baseline 永不建立 → 摊牌 skip。
        # 在 hand 起始时(无 overlay 状态)强制建一次 baseline。
        self._initialize_avatar_baselines()

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
                # OCR 失败 fallback (2026-05-27):用 avatar hash 派生跨手稳定身份.
                # 让 Path B 统计能把"同一物理玩家"跨手聚合(即使我们不知道他叫啥).
                # 用户可通过 dashboard / player_registry.json 手动改名为真实昵称.
                # 命中条件:avatar_hash 非空 AND 此 hash 未在 _avatar_fingerprints 注册过
                # (若已注册,line 423-435 avatar 匹配路径会先消化掉,根本走不到这里).
                if avatar_hash:
                    temp_name = f"TempUser_{avatar_hash[:8]}"
                    self.tracker.player_id_map[seat.seat_index] = temp_name
                    self.tracker._avatar_fingerprints[avatar_hash] = temp_name
                    logger.info(f"_capture_player_ids: seat_{seat.seat_index} OCR 失败,"
                                f"派生 {temp_name}(avatar hash)")
                    diag.emit("player.tempuser_assigned",
                              {"seat": seat.seat_index, "hash_prefix": avatar_hash[:8],
                               "temp_name": temp_name},
                              hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None)
                continue
            # Filter: if text parses as action keyword, it's transition-frame
            # contamination, not a real player nickname.
            if self.action_recognizer.parse(text) is not None:
                logger.debug(f"_capture_player_ids: seat_{seat.seat_index} got action-text "
                             f"{text!r}, skipping (likely transition frame)")
                # 同样:文字噪声不可信 → 走 avatar hash fallback
                if avatar_hash and avatar_hash not in self.tracker._avatar_fingerprints:
                    temp_name = f"TempUser_{avatar_hash[:8]}"
                    self.tracker.player_id_map[seat.seat_index] = temp_name
                    self.tracker._avatar_fingerprints[avatar_hash] = temp_name
                    diag.emit("player.tempuser_assigned",
                              {"seat": seat.seat_index, "hash_prefix": avatar_hash[:8],
                               "temp_name": temp_name, "reason": "action_text_contamination",
                               "raw_text": text},
                              hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None)
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
        # Apply to player_id_map AND sync DB (UPDATE historical action_events).
        # Without DB sync,past rows永远是旧 alias,path B 聚合统计仍把两个名当独立玩家。
        db_updates: dict[str, str] = {}  # old_name → canonical
        for sidx, current in list(self.tracker.player_id_map.items()):
            new = canonical.get(current, current)
            if new != current:
                logger.info(f"_canonicalize: seat_{sidx} {current!r} → {new!r} (alias merged)")
                self.tracker.player_id_map[sidx] = new
                db_updates[current] = new
        # Cross-name canonical(handle name→name pairs that may not be in player_id_map)
        for old, new in canonical.items():
            if old != new and old not in db_updates:
                db_updates[old] = new
        if db_updates and self._db_enabled:
            try:
                with SessionLocal() as session:
                    total_rows = 0
                    for old, new in db_updates.items():
                        result = session.execute(
                            sql_text("UPDATE action_events SET player_name = :new "
                                     "WHERE player_name = :old"),
                            {"new": new, "old": old},
                        )
                        total_rows += result.rowcount or 0
                    session.commit()
                    if total_rows > 0:
                        logger.info(f"_canonicalize: DB updated {total_rows} action_events "
                                    f"rows for aliases {list(db_updates.items())}")
            except Exception:
                logger.warning("canonicalize DB UPDATE failed", exc_info=True)

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

    def _detect_hero_seat_index(self, rois) -> int | None:
        """检测 hero 自己的座位 index(几何上 seat.cards_area 与 hero_card_1 重叠)。

        坐下模式:返回 hero 所在 seat_index(通常 0)。
        观战模式:--observer flag 或 hero_card_1 与所有 seat 都不重叠 → 返回 None。
        缓存于 tracker._hero_seat_idx_cache 以避免每 tick 重算。
        """
        cached = getattr(self.tracker, "_hero_seat_idx_cache", "uninitialized")
        if cached != "uninitialized":
            return cached
        # 观战模式 short-circuit:用户未坐下,不该跳任何 seat
        if self.observer_mode:
            self.tracker._hero_seat_idx_cache = None
            return None
        hc = rois.hero_card_1
        result: int | None = None
        if hc is not None and hc.width > 0 and hc.height > 0:
            hx1, hy1 = hc.left, hc.top
            hx2, hy2 = hx1 + hc.width, hy1 + hc.height
            for seat in rois.seat_regions:
                ca = seat.cards_area
                if ca is None or ca.width == 0:
                    continue
                sx1, sy1 = ca.left, ca.top
                sx2, sy2 = sx1 + ca.width, sy1 + ca.height
                # bbox 重叠判定
                if not (sx2 < hx1 or sx1 > hx2 or sy2 < hy1 or sy1 > hy2):
                    result = seat.seat_index
                    break
        self.tracker._hero_seat_idx_cache = result
        if result is not None:
            logger.info(f"[hero-seat] 检测到 hero 座位 seat_{result},摊牌捕获将跳过该 seat")
            diag.emit("showdown.hero_seat_detected",
                      {"hero_seat_index": result, "mode": "sitting"},
                      hand_id=None)
        else:
            logger.info("[hero-seat] 未检测到 hero 座位(可能观战模式或 hero_card_1 未配置)")
            diag.emit("showdown.hero_seat_detected",
                      {"hero_seat_index": None, "mode": "observer"},
                      hand_id=None)
        return result

    # Gate constants shared by live-capture + hand-end aggregator
    _SHOWDOWN_BASELINE_DIVERGE_THRESHOLD = 6  # hamming > 6 of 64 bits = overlay visible
    _SHOWDOWN_CONF_THRESHOLD = 0.9            # per-card CNN conf gate
    _SHOWDOWN_CNN_THROTTLE_SEC = 1.0          # min seconds between CNN runs per seat
    # cards_area mean brightness threshold:真牌图白底 → ~180-210;
    # 下注阶段桌面暗色 / 头像 → ~40-80.物理硬约束,几乎不可能误判.
    # 没这个门 fold_area diverged 会在河牌下注阶段(timer 数字 / 弃牌文字 / All in)
    # 大量误触发 → cards_area 内还没有真牌就被抓走 dump.
    _SHOWDOWN_CARDS_BRIGHTNESS_MIN = 120
    # cards_area 纹理细节门:真牌正面有 rank / suit / 角标等丰富细节 → std > 60;
    # 卡背是均匀重复花纹 → std 25-45.阈值 50 区分清晰.
    # 这是治本卡背"高 conf 误报"(罕见但污染统计)+ 副产物 GPU 降 50%+
    _SHOWDOWN_CARDS_TEXTURE_MIN = 50

    def _try_capture_showdown_live(self, rois) -> None:
        """Per-tick (called from main loop) — capture showdown cards WHILE the
        overlay is visible, not at hand-end (when UI has already closed).

        Architecture (2026-05-26 root-cause fix):
          Old: _capture_showdown_cards called once from _end_current_hand →
               community already reset, overlay gone, captured avatar pixels.
               Diagnostic proof: fixtures mean_R=205 vs dumps mean_R=40,
               luminance chi-square=1.21 (>6× the "significant gap" threshold).
          New: this method runs EVERY tick during river phase. Detects overlay
               via fold_area hash divergence from idle baseline. Per-seat
               throttle prevents CNN spam.  Multiple captures per hand allowed
               (training-data harvest); first CNN-passing result wins the
               authoritative `tracker._showdown_captured_this_hand[sidx]`.

        Cheap-fast path early returns ensure non-river ticks cost ~µs.
        """
        hand = self.tracker.current_hand
        if hand is None:
            return
        # River-only gate (no point trying earlier streets)
        from events.models import Street
        community = hand.community_cards.get(Street.RIVER) if hand.community_cards else None
        if not community or len(community) < 5:
            return
        # ≥ 2 non-folded active sanity (skip fold-around hands)
        active = self.tracker._seats_with_events_this_hand - self.tracker._folded_seats
        if len(active) < 2:
            return

        from collections import deque
        now = time.time()

        # T-seat0-fix(2026-05-28):自动检测 hero seat 并跳过摊牌捕获。
        # 根因:seat[0].cards_area 几何上与 hero_card_1 重合 = hero 自己的座位。
        # Hero 牌正面朝上无"翻牌瞬间",且摊牌阶段 UI 被 amount / 庆祝动画覆盖,
        # brightness gate 100% 拦截 → 49 次摊牌触发 0 accepted,数据假阳性污染统计。
        # 修复:摊牌捕获跳过与 hero_card_1 几何重叠的 seat。
        hero_seat_idx = self._detect_hero_seat_index(rois)

        for seat in rois.seat_regions:
            sidx = seat.seat_index
            if sidx == hero_seat_idx:
                # Hero 自己的牌走 rois.hero_card_1/2 独立捕获,摊牌主链路不重复处理
                continue
            if sidx in self.tracker._folded_seats:
                continue
            # Throttle: limit per-seat CNN to 1 Hz
            last_at = self.tracker._showdown_last_cnn_at.get(sidx, 0.0)
            if now - last_at < self._SHOWDOWN_CNN_THROTTLE_SEC:
                continue
            if seat.fold_area is None or seat.fold_area.width == 0:
                continue
            # Hash check on fold_area — diverged = overlay visible (cards / "弃牌" / timer / etc.)
            fold_img_now = self.capturer.capture_roi(seat.fold_area)
            if fold_img_now is None or fold_img_now.size == 0:
                continue
            current_hash = _avg_hash_64(fold_img_now)
            baseline = self.tracker._idle_avatar_hash.get(sidx)
            if baseline is None:
                continue
            diff = _hamming(current_hash, baseline)
            if diff < self._SHOWDOWN_BASELINE_DIVERGE_THRESHOLD:
                continue  # no overlay — quiet tick for this seat
            # Diverged → likely showdown cards visible right now.  Capture cards_area.
            if seat.cards_area is None or seat.cards_area.width == 0:
                continue
            img = self.capturer.capture_roi(seat.cards_area)
            if img is None or img.size == 0:
                continue
            h, w = img.shape[:2]
            if w < 40 or h < 40:
                continue
            # Brightness gate (2026-05-26 residual fix):cards_area must look like
            # bright cards (white background ~180+) not dark table felt / avatar
            # leak (~40-80).Physical hard constraint, no gray zone.
            # Without this, fold_area diverged fires during river betting (timer text,
            # "弃牌", "All in") → cards_area still empty → mass non-card dumps.
            mean_brightness = float(img.mean())
            if mean_brightness < self._SHOWDOWN_CARDS_BRIGHTNESS_MIN:
                diag.emit("showdown.dark_cards_area",
                          {"seat": sidx, "mean_brightness": round(mean_brightness, 1),
                           "fold_hamming": diff,
                           "threshold": self._SHOWDOWN_CARDS_BRIGHTNESS_MIN},
                          hand_id=hand.id)
                # Don't mark throttle — let next tick re-check (overlay may still be transient)
                continue
            # Texture gate (2026-05-27):亮但均匀 = 卡背(均匀花纹)而非卡正面.
            # 卡背 std 25-45,卡正面 std 60-90,阈值 50 安全区分.
            std_val = float(img.std())
            if std_val < self._SHOWDOWN_CARDS_TEXTURE_MIN:
                diag.emit("showdown.uniform_back",
                          {"seat": sidx, "mean_brightness": round(mean_brightness, 1),
                           "std": round(std_val, 1),
                           "threshold": self._SHOWDOWN_CARDS_TEXTURE_MIN},
                          hand_id=hand.id)
                continue
            # Mark throttle only after passing brightness + texture — real CNN attempt about to happen
            self.tracker._showdown_last_cnn_at[sidx] = now

            card_zone = img[: int(h * 0.8), :]
            left_img = card_zone[:, : w // 2]
            right_img = card_zone[:, w // 2 :]
            left_card = self.card_recognizer.recognize_single(left_img)
            right_card = self.card_recognizer.recognize_single(right_img)
            # Training-data harvest: dump every capture regardless of CNN outcome
            self._dump_showdown_crop(hand.id, sidx, "L", left_img, left_card, diff)
            self._dump_showdown_crop(hand.id, sidx, "R", right_img, right_card, diff)

            # If already accepted earlier this hand, keep harvesting dumps but skip re-decision
            if sidx in self.tracker._showdown_captured_this_hand:
                continue

            # Run through conf + physical + history gates
            cards = []
            for c in (left_card, right_card):
                if not c:
                    continue
                rc = c.get("rank_conf", 1.0)
                sc = c.get("suit_conf", 1.0)
                if rc < self._SHOWDOWN_CONF_THRESHOLD or sc < self._SHOWDOWN_CONF_THRESHOLD:
                    diag.emit("showdown.gate5_low_conf",
                              {"seat": sidx, "card": f"{c['rank']}{c['suit']}",
                               "rank_conf": round(rc, 3), "suit_conf": round(sc, 3),
                               "threshold": self._SHOWDOWN_CONF_THRESHOLD},
                              hand_id=hand.id)
                    continue
                cards.append(f"{c['rank']}{c['suit']}")

            if len(cards) == 2:
                if cards[0] == cards[1]:
                    diag.emit("showdown.gate6a_physical_violation",
                              {"seat": sidx, "cards": cards}, hand_id=hand.id, level="WARN")
                    continue
                pred_tuple = (cards[0], cards[1])
                hist = self.tracker._seat_pred_history.setdefault(sidx, deque(maxlen=5))
                hist.append(pred_tuple)
                if len(hist) >= 3 and hist.count(pred_tuple) >= 3:
                    diag.emit("showdown.gate6b_hallucination",
                              {"seat": sidx, "cards": list(pred_tuple),
                               "occurrences": hist.count(pred_tuple), "window": len(hist)},
                              hand_id=hand.id, level="WARN")
                    continue
                # T4 Gate 6c (2026-05-27):hole vs community + cross-seat uniqueness.
                # 单牌堆扑克物理约束:同一张牌不能既在公共牌又在手牌,也不能跨座位重复.
                # 此前 12-15% accepted 摊牌违反此约束 → 显式拒绝并落 diagnostic.
                community_now = hand.community_cards.get(Street.RIVER) or []
                existing_seats_cards = [
                    c for other_cards in self.tracker._showdown_captured_this_hand.values()
                    for c in other_cards
                ]
                gate6c_violations: list[str] = []
                for card in cards:
                    if card in community_now:
                        gate6c_violations.append(f"{card} in community")
                    if card in existing_seats_cards:
                        gate6c_violations.append(f"{card} duplicate across seats")
                if gate6c_violations:
                    diag.emit(
                        "showdown.gate6c_physical_violation",
                        {
                            "seat": sidx,
                            "cards": cards,
                            "violations": gate6c_violations,
                            "community": list(community_now),
                            "existing_seats": dict(self.tracker._showdown_captured_this_hand),
                        },
                        hand_id=hand.id,
                        level="WARN",
                    )
                    continue
                # Accepted — store and emit
                self.tracker._showdown_captured_this_hand[sidx] = cards
                logger.info(f"[showdown live] seat_{sidx} cards: {cards} (avatar hamming={diff})")
                diag.emit("showdown.accepted",
                          {"seat": sidx, "cards": cards, "avatar_hamming": diff},
                          hand_id=hand.id)
            elif cards:  # 0 or 1 card passed conf
                diag.emit("showdown.incomplete",
                          {"seat": sidx, "cards_passed_conf": cards}, hand_id=hand.id)

    def _capture_showdown_cards(self) -> dict[int, list[str]]:
        """Hand-end aggregator: returns what live-capture accumulated this hand.

        Architecture (2026-05-26): the real work happens in
        _try_capture_showdown_live (per-tick during river).  This method only:
          1. Reads tracker._showdown_captured_this_hand
          2. Emits one hand-level diag summary (gate1_skip / gate2_skip / enter)

        Returns empty dict if hand was fold-around-pre-river or had no
        non-folded active seats.  Otherwise returns the accepted cards by seat.
        """
        hand = self.tracker.current_hand
        if hand is None:
            return {}
        from events.models import Street
        community = hand.community_cards.get(Street.RIVER) if hand.community_cards else None
        river_count = len(community) if community else 0
        active = self.tracker._seats_with_events_this_hand - self.tracker._folded_seats
        captured = dict(self.tracker._showdown_captured_this_hand)

        if river_count < 5:
            diag.emit("showdown.gate1_skip",
                      {"reason": "community_lt_5", "river_count": river_count},
                      hand_id=hand.id)
            return {}
        if len(active) < 2:
            diag.emit("showdown.gate2_skip",
                      {"reason": "active_lt_2", "active_count": len(active),
                       "active": sorted(active), "folded": sorted(self.tracker._folded_seats)},
                      hand_id=hand.id)
            return {}
        diag.emit("showdown.enter",
                  {"active_seats": sorted(active), "river_count": river_count,
                   "captured_count": len(captured),
                   "captured_seats": sorted(captured.keys())},
                  hand_id=hand.id)
        return captured

    def _dump_showdown_crop(self, hand_id, sidx: int, side: str,
                            img: np.ndarray, pred: dict | None, hamming: int) -> None:
        """Save one card crop + metadata sibling for training-data harvest.

        Always writes when SHOWDOWN_DUMP_ENABLED — covers CNN-rejected cases too,
        so the labeling tool can show "CNN was wrong here (conf X) → real card is Y".
        data/ is gitignored;disk cost ≈ 5KB per crop, ~10 crops/showdown.
        """
        if not SHOWDOWN_DUMP_ENABLED or img is None or img.size == 0:
            return
        try:
            dump_dir = Path("data/showdown_dumps") / str(hand_id)
            dump_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%H%M%S%f")[:-3]  # ms precision
            stem = f"seat_{sidx}_{side}_{ts}"
            cv2.imwrite(str(dump_dir / f"{stem}.png"), img)
            meta = {
                "hand_id": str(hand_id),
                "seat": sidx,
                "side": side,
                "avatar_hamming": hamming,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "cnn_prediction": (
                    {"rank": pred["rank"], "suit": pred["suit"],
                     "rank_conf": round(pred.get("rank_conf", 1.0), 4),
                     "suit_conf": round(pred.get("suit_conf", 1.0), 4)}
                    if pred else None
                ),
            }
            with open(dump_dir / f"{stem}.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.debug(f"showdown dump failed seat_{sidx} side={side}", exc_info=True)

    def _initialize_avatar_baselines(self) -> None:
        """Ensure every seat with fold_area has an avatar baseline at hand-start.

        Reasoning: per-tick baseline update only fires when fold_area returns
        empty (idle state). High-activity players have very few empty ticks →
        baseline never establishes → showdown gate always skips them. By forcing
        baseline capture at hand-start (when no overlay is active yet), we
        guarantee every configured seat has SOMETHING to compare against at
        showdown. Subsequent per-tick updates still refine during idle.

        Only initializes;does NOT overwrite existing baselines (those came from
        confirmed idle moments and are higher quality).
        """
        for seat in self.roi_manager.rois.seat_regions:
            sidx = seat.seat_index
            if sidx in self.tracker._idle_avatar_hash:
                continue  # already established, don't overwrite
            if seat.fold_area is None or seat.fold_area.width == 0:
                continue
            img = self.capturer.capture_roi(seat.fold_area)
            if img is None or img.size == 0:
                continue
            self.tracker._idle_avatar_hash[sidx] = _avg_hash_64(img)
            logger.debug(f"_initialize_avatar_baselines: seat_{sidx} baseline set at hand-start")

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
                # 2026-05-27 EXCEPTION:stack=0 是合法 all-in 状态,不视为 OCR jump → 不拒收.
                # 之前这条 sanity 误把 all-in 当成 OCR 错误,导致 stack_after=0 永远不出现 →
                # all_in.detected 永远 = 0(根因 D).
                prev_stack = self.tracker._prev_stack.get(sidx)
                if (stack_now is not None and stack_now > 0  # ← 关键加 stack_now > 0
                        and prev_stack is not None and prev_stack > 0
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

            # Branch 0 (2026-05-26): dedicated timer_area takes priority when configured.
            # Smaller ROI = focused digit OCR, more accurate + faster.
            # Falls through to fold_area path if timer not detected (or timer_area unconfigured).
            timer_handled = False
            if seat_roi.timer_area is not None and seat_roi.timer_area.width > 0:
                timer_img = self.capturer.capture_roi(seat_roi.timer_area)
                timer_text = self.ocr.read_text(timer_img, allowlist="0123456789s ")
                tm = re.search(r"\b(\d{1,2})\b", timer_text or "") if timer_text else None
                if tm and 0 <= int(tm.group(1)) <= 60:
                    self._process_timer(sidx, int(tm.group(1)))
                    if stack_now is not None:
                        self.tracker._prev_stack[sidx] = stack_now
                    timer_handled = True
            if timer_handled:
                continue

            if seat_roi.fold_area is not None:
                fold_img = self.capturer.capture_roi(seat_roi.fold_area)
                fold_text = self.ocr.read_text(fold_img)
                ft = fold_text.strip() if fold_text else ""
                # Bug 3 fix: regex extracts digits even with surrounding noise
                # (e.g. "15 sec" / "15." / " 15"). Permissive but bounded to
                # 0-60 (reasonable timer range).
                timer_match = re.search(r"\b(\d{1,2})\b", ft) if ft else None
                # Branch 1: digit found and looks like timer → countdown
                # Skip if dedicated timer_area already handled (logic above).
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
                # 2026-05-27 root-cause C:OCR 在 all-in 瞬间常因 "All in" 大字覆盖 stack
                # → stack OCR 返 None → stack_after=None → P3 stack-derived 走错路径,
                # text-derived ALL_IN 反而被覆盖.解决:文字侧已识别为 ALL_IN 时,假定
                # stack_after = 0(物理意义:all-in = stack 清零).
                if stack_after is None and parsed["action_type"] == ActionType.ALL_IN:
                    stack_after = 0
                    logger.debug(f"seat_{sidx} all-in text + stack OCR None → 假定 stack_after=0")
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
                # T3 + T3 续集(2026-05-28):text-derived 是 WePoker UI 文字 ground
                # truth(明确按钮文字"弃牌"/"过牌"/"跟注"/"下注"/"加注"/"All in")。
                # Stack-derived 通过 stack_delta + current_to_call 推断,但:
                #   - stack_delta=0 时 FOLD vs CHECK 无法区分(T3)
                #   - first_bet_this_street 跟踪在某些 case 错 → BET ↔ RAISE ↔ CALL
                #     三角误推(T3 续集,baseline 数据证实 bet 准确率 0%)
                #
                # 新策略:text 跟 stack delta 物理签名"一致"时,text 优先;
                # 仅在物理矛盾时(text OCR 错)stack 才 override。
                #
                # 物理签名分组:
                #   zero contribution: FOLD / CHECK         (stack_delta ≈ 0)
                #   chip contribution: CALL / BET / RAISE   (stack_delta > 0)
                #   all-in special:    ALL_IN               (stack_after ≈ 0)
                text_is_zero = text_derived in (ActionType.FOLD, ActionType.CHECK)
                text_is_chip = text_derived in (ActionType.CALL, ActionType.BET, ActionType.RAISE)
                stack_is_zero = (stack_delta is not None and abs(stack_delta) <= 2)
                stack_is_chip = (stack_delta is not None and abs(stack_delta) > 2)
                text_stack_consistent = (
                    (text_is_zero and stack_is_zero)
                    or (text_is_chip and stack_is_chip)
                )

                if (
                    stack_derived is not None
                    and stack_derived != text_derived
                    and not text_stack_consistent
                ):
                    # 物理矛盾(text OCR 可能错)→ stack 优先
                    final_action = stack_derived
                    override_reason = f"stack-derived {stack_derived.value} overrode text-derived {text_derived.value}"
                    logger.info(f"[P3 override] seat_{sidx} text={action_text!r} "
                                f"text→{text_derived.value} stack→{stack_derived.value}")
                elif (
                    stack_derived is not None
                    and stack_derived != text_derived
                    and text_stack_consistent
                ):
                    # 物理签名一致但 stack-derived 内部歧义(fold/check 或 bet/raise/call 三角)
                    # → 保留 text-derived,落 diagnostic 便于回溯
                    ambig_type = "fold_check" if text_is_zero else "bet_raise_call"
                    diag.emit(
                        "p3.text_stack_internal_ambiguity",
                        {
                            "seat": sidx,
                            "text_action": text_derived.value,
                            "stack_action": stack_derived.value,
                            "stack_delta": stack_delta,
                            "current_to_call": self.tracker._street_to_call,
                            "ambiguity_type": ambig_type,
                        },
                        hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None,
                    )

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
                    diag.emit("all_in.detected",
                              {"seat": sidx, "player": player_name,
                               "final_action": final_action.value,
                               "stack_before": stack_before, "stack_after": stack_after,
                               "stack_delta": stack_delta, "action_text": action_text},
                              hand_id=self.tracker.current_hand.id)
                # Detection-gap probe: if OCR text contains "all" / "全押" but final_action
                # is not all_in (means stack OCR missed reading 0 → P3 inference fell through),
                # log a candidate so we can later diagnose why the explicit signal didn't lift.
                elif action_text and any(k in action_text.lower() for k in ("all in", "all-in", "allin", "全押")):
                    diag.emit("all_in.text_only_candidate",
                              {"seat": sidx, "player": player_name,
                               "final_action": final_action.value,
                               "stack_before": stack_before, "stack_after": stack_after,
                               "stack_delta": stack_delta, "action_text": action_text},
                              hand_id=self.tracker.current_hand.id, level="WARN")

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

        # Hand-start signal via 总底池 label (observer-mode fallback when hero ROI
        # is unchanged + community-reset window is too narrow for 250 ms tick).
        # User-confirmed:"总底池" 文本只在新手开始时短暂出现.
        # Triple-guard against false positive:
        #   (1) label present in OCR text
        #   (2) there is an active hand to end
        #   (3) new amount < 50% of hand_pot_peak (real new-hand pot is much smaller)
        # All three must hold;mid-hand stray "总底池" misread alone won't trigger.
        if (pot_text and "总底池" in pot_text
                and self.tracker.has_active_hand
                and amount is not None
                and self.tracker._hand_pot_peak is not None
                and amount < self.tracker._hand_pot_peak * 0.5):
            old_peak = self.tracker._hand_pot_peak
            old_hand_id = self.tracker.current_hand.id if self.tracker.current_hand else None
            logger.info(f"[hand-start] 总底池 label + pot drop {old_peak}→{amount}, "
                        f"ending previous hand")
            diag.emit("hand_start.via_pot_label",
                      {"old_pot_peak": old_peak, "new_pot": amount, "pot_text": pot_text},
                      hand_id=old_hand_id)
            hero_1 = self.capturer.capture_roi(rois.hero_card_1) if rois.hero_card_1 else None
            hero_2 = self.capturer.capture_roi(rois.hero_card_2) if rois.hero_card_2 else None
            self._end_current_hand(db)
            self._start_new_hand(db, hero_1, hero_2)
            # start_new_hand reset latest_pot_bb=None and _hand_pot_peak=None;
            # the guard below will pass and amount becomes the new hand's first pot reading.

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
        """Scan each seat's button_indicator ROI to find dealer button (seat_index).

        T13 fix (2026-05-28):
          原 L1-only OCR 在 20×22 像素单字符上一直 fail → fallback seat=0,
          position 全错(seat-fixed,不轮转)。794+ 手数据 button_seat_index NULL。
        新 3 层 fallback + diag emit:
          L1: OCR "D" 直接命中(理想情况)
          L2: brightness peak — button icon 通常高对比,亮度 outlier
          L3: 全部 fail → fallback seat=0(同原行为)+ 落 diag WARN
        """
        candidates = []  # (seat_index, ocr_text, brightness)
        button_seat = None
        method = None

        for seat_roi in self.roi_manager.rois.seat_regions:
            if seat_roi.button_indicator is None:
                continue
            img = self.capturer.capture_roi(seat_roi.button_indicator)
            if img.size == 0:
                continue
            text = self.ocr.read_text(img, allowlist="D")
            brightness = float(img.mean())
            candidates.append((seat_roi.seat_index, text, brightness))
            # L1: OCR D 命中
            if "D" in text.upper() and button_seat is None:
                button_seat = seat_roi.seat_index
                method = "L1-ocr"

        # L2: brightness peak fallback(OCR 全 fail 时)
        if button_seat is None and len(candidates) >= 2:
            sorted_by_b = sorted(candidates, key=lambda x: x[2], reverse=True)
            max_b = sorted_by_b[0][2]
            second_b = sorted_by_b[1][2]
            # ratio 1.5 = button 区域显著比其他亮(outlier 检测)
            if second_b > 0 and max_b / second_b >= 1.5:
                button_seat = sorted_by_b[0][0]
                method = "L2-brightness"

        if button_seat is None:
            button_seat = 0
            method = "L3-fallback"
            logger.warning(f"Button detection 全 fail,fallback seat=0. Candidates: {candidates}")
        else:
            logger.info(f"Button detected at seat {button_seat} via {method}")

        # T13 diag:每手记录 button 检测方法 + 候选,便于事后审视
        diag.emit(
            "button.detected",
            {
                "button_seat": button_seat,
                "method": method,
                "candidates": [{"seat": c[0], "ocr": c[1], "brightness": round(c[2], 1)} for c in candidates],
            },
            hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None,
            level="INFO" if method != "L3-fallback" else "WARN",
        )

        self.roi_manager.button_seat_index = button_seat
        # T13:把 button_seat_index 落 hand.raw_data,便于 audit / dashboard 用
        if self.tracker.current_hand is not None:
            if self.tracker.current_hand.raw_data is None:
                self.tracker.current_hand.raw_data = {}
            self.tracker.current_hand.raw_data["button_seat_index"] = button_seat
            self.tracker.current_hand.raw_data["button_detection_method"] = method

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
