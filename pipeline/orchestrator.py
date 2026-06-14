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
from config import CAPTURE_INTERVAL_MS, ROI_CONFIG_DIR, ROI_PROFILE, VERBOSE_DIAG, BATCH_SEAT_OCR, DIGIT_RECIPE_LIVE, FRAME_CAPTURE, BUTTON_CUT, OCR_RECOGNIZE_ONLY, ACTION_PHASH_LIVE, SEAT_OCCUPANCY_LIVE, ALLIN_STACKZERO, ALLIN_ZERO_RUN, ALLIN_DEFER_WRITE, SHOWDOWN_GATE, SHOWDOWN_WHITE_TH, POT_LABEL_WHITE_TH, POT_LABEL_TEAL_TH
from pipeline.reconstruct import button_move_online, reconcile_underread_amount, blinds_from_button, reconstruct_hand_chips, pot_debounce_step
from pipeline.label_capture import LabelCapturer  # 信源验证标注采集侧(LABEL_SIGNAL 空时全 no-op)
from difflib import get_close_matches

import cv2
import numpy as np

from events.models import ActionType, Position
from events.normalizer import compute_confidence, infer_action_from_delta
from events import diag


def allin_stackzero_step(state, stack_now, is_active, run_th=1):
    """#243 影子 all-in 检测【纯逻辑】(Linux 可单测)。state={'last_pos','zero_run','emitted','was_active'}。
    online 版 probe_stack_zero.find_episodes:确认正数→复位;0→游程+1;None→不增不减(遇遮挡不打断、只数真0)。
    闸=**本手曾活跃(was_active latch)**——非瞬时(all-in后摊牌card_marker消失会让座瞬间掉出活跃集,
    第一版用瞬时闸→2小时0命中)。run_th=1:首个0即发(实测无单帧0噪声)。
    ≥run_th 帧 0 + 本手曾活跃 + 有归0前正数 + 本手未发 → 返 (True, amount=归0前最后正数)。"""
    if is_active:
        state['was_active'] = True          # latch:本手持过牌
    if stack_now is not None and stack_now > 0:
        state['last_pos'] = stack_now
        state['zero_run'] = 0
        return (False, None)
    if stack_now == 0:
        state['zero_run'] = state.get('zero_run', 0) + 1
    if (state.get('zero_run', 0) >= run_th and not state.get('emitted')
            and state.get('was_active') and state.get('last_pos')):
        state['emitted'] = True
        return (True, state['last_pos'])
    return (False, None)

# Allowlist for action_area OCR — restricts charset to known action keywords + amounts.
# Filters out garbage like "疯鱼罩轩 2"(player name bleed)or random Chinese characters.
#
# T61(2026-05-29):6-char 极致版 — 用户实测确认 action_area 真实只 4 词
# (跟注 / 让牌 / 加注 / 下注),弃牌走 fold_area / ALL IN 走 fold_area.
# 6 chars 去重后覆盖全部 4 个真 action 词.
# 配合 T60 compute_confidence 物理矛盾兜底:6-char 可能洗白噪音
# (`全加注` → `加注`,`下盖注` → `下注`),conf=0.3 自动拦,raw_data 留证.
ACTION_OCR_ALLOWLIST = "跟注让牌加下"


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
        # T23 (2026-05-28):必须显式 encoding="utf-8",否则 Windows 默认 cp936
        # 写中文 player_name → baseline 工具 utf-8 读时 UnicodeDecodeError。
        with open(hand_dir / f"{prefix}_meta.json", "w", encoding="utf-8") as f:
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
        with open(PLAYER_REGISTRY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.warning(f"Failed to load {PLAYER_REGISTRY_PATH}, starting fresh", exc_info=True)
        return {"fingerprints": {}}


def _save_player_registry(fingerprints: dict) -> None:
    try:
        PLAYER_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PLAYER_REGISTRY_PATH, "w", encoding="utf-8") as f:
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
            # Fallback: check the JSON data(json 已模块级导入,勿在此 import → 否则整 __init__ 视 json 为局部)
            with open(roi_path, encoding="utf-8") as f:
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
        # T72(2026-05-29):config.USE_GPU 控制 EasyOCR GPU 模式.
        # 单 OCR 引擎(2026-06-06 P3:删 双OCR/attention 实验,ocr_focus 退役)。
        # 调用方每次 read_text(..., allowlist=...) 覆盖,故 default_allowlist="" 即可。
        from config import USE_GPU
        self.ocr = OCREngine(gpu=USE_GPU, name="global", default_allowlist="")

        # 2026-06-05 杠杆A → 2026-06-06 各区独立模板:DIGIT_RECIPE_LIVE 时配方接管数字读取。
        # 用户拍板「各区用自己模板」(治 cut1 用 stack 模板读 amount 的 3↔4;零跨区耦合)。
        # 文件名 digit_templates_{profile}_{zone}.json,缺则回退 default(stack)→ 向后兼容、
        # 未采的区不退化。_digit_reader = default/stack(仍作"配方是否生效"哨兵)。
        self._digit_reader = None       # default(=stack)reader;热路径 guard 仍看它
        self._zone_readers: dict = {}   # {zone: DigitReader} 各区独立
        if DIGIT_RECIPE_LIVE:
            from pipeline.digit_reader import DigitReader
            base = Path(ROI_CONFIG_DIR)
            for zone in ("stack", "amount", "pot", "potprev", "herobet", "winxx"):
                f = base / f"digit_templates_{profile}_{zone}.json"
                if f.is_file():
                    self._zone_readers[zone] = DigitReader.load(str(f))
                    logger.info(f"[各区模板] {zone} ← {f.name}")
            # default/stack:优先 _stack.json,否则旧单文件 digit_templates_{profile}.json
            if "stack" in self._zone_readers:
                self._digit_reader = self._zone_readers["stack"]
            else:
                legacy = base / f"digit_templates_{profile}.json"
                if legacy.is_file():
                    self._digit_reader = DigitReader.load(str(legacy))
                    self._zone_readers["stack"] = self._digit_reader
                    logger.info(f"[各区模板] stack ← {legacy.name}(legacy 单文件)")
            if self._digit_reader is None:
                logger.warning("[各区模板] DIGIT_RECIPE_LIVE=1 但无任何模板 → 仍用 EasyOCR")
            else:
                logger.info(f"[各区模板] 已载 {sorted(self._zone_readers)};EasyOCR 仅兜底")

        # 2026-06-07 #240:动作识别走 text-shape phash 二元桩(替 action OCR)。哨兵 _action_phash;
        # 参考文件 rois/action_refs_{profile}.json(tools/build_action_refs.py 产)。缺则保持 None → 回退 OCR。
        self._action_phash = None
        if ACTION_PHASH_LIVE:
            from pipeline.action_phash import ActionPhashReader
            apf = Path(ROI_CONFIG_DIR) / f"action_refs_{profile}.json"
            if apf.is_file():
                self._action_phash = ActionPhashReader.load(str(apf))
                logger.info(f"[动作phash] 已载 {apf.name}: {{ {', '.join(f'{w}:{len(h)}' for w, h in self._action_phash.refs.items())} }}"
                            f" 阈值={self._action_phash.threshold} → 替 action OCR")
            else:
                logger.warning(f"[动作phash] ACTION_PHASH_LIVE=1 但无 {apf.name} → 仍用 action OCR(先跑 build_action_refs.py)")

        # 2026-06-08 #241(rebuy 前置):每座占用判定 = live 区域 avg_hash vs 空桌基线。
        # 哨兵 _empty_refs(rois/empty_refs_{profile}.json,build_empty_refs.py 产)。缺则 None → 跳过。
        self._empty_refs = None
        self._occupancy_th = int(os.getenv("POKEMIR_OCCUPANCY_TH", "18"))  # 录像验:空隙16-24,18一刀两断
        # +xx 黄色特异检测(替 avg_hash:avg_hash 全面过火3.3座/手、s0误报75%)。win_amount 区黄像素
        # 【数量】> 阈 = +xx。用数量不用占比:录像验数量空隙15-60(超宽)、占比空隙仅0.01-0.02(窄,稀释漏报)。
        self._win_yellow_count = int(os.getenv("POKEMIR_WIN_YELLOW_COUNT", "35"))  # 录像验空隙15-60,35居中
        self._yellow_hsv = (18, 38, 70, 120)  # H[lo,hi] S>min V>min(cv2),probe 验过
        self._hand_win_seats = set()  # 本手 +xx latch(per-tick 扫,某 tick 见到就锁;_start_new_hand 重置)
        self._hand_win_amounts = {}   # 本手 +xx 金额 latch(seat→float;喂 hands.result;_start_new_hand 重置)
        self._hand_start_tick = 0     # 本手起 global tick;+xx 跳发牌窗(牌背飞=开局)用
        self._xx_deal_skip = int(os.getenv("POKEMIR_XX_DEAL_SKIP", "3"))  # 跳每手前 N tick(发牌窗,治s0牌背飞)
        if SEAT_OCCUPANCY_LIVE:
            erf = Path(ROI_CONFIG_DIR) / f"empty_refs_{profile}.json"
            if erf.is_file():
                with open(erf, encoding="utf-8") as f:
                    self._empty_refs = json.load(f).get("seats", {})
                logger.info(f"[占用] 已载 {erf.name}: {len(self._empty_refs)} 座空桌基线 阈={self._occupancy_th} → 每手判占用")
            else:
                logger.warning(f"[占用] POKEMIR_SEAT_OCCUPANCY=1 但无 {erf.name} → 跳过(先跑 build_empty_refs.py)")

        # 2026-06-06 step 2b:按钮权威切手在线去抖状态(BUTTON_CUT 开时用)。
        # confirmed=已确认按钮座;pending/_count=候选顺时针新座的连续帧计数(见
        # reconstruct.button_move_online)。跨手持续,不在 _start_new_hand 重置。
        self._btn_confirmed = None
        self._btn_pending = None
        self._btn_pending_count = 0
        # #242 飞行窗预测基:上一手实际落定的按钮座(供 _predict_next_button 顺时针推进)。
        self._last_button_seat = None
        if BUTTON_CUT:
            logger.info("[step2b] BUTTON_CUT=1:换手走按钮权威(白占比+在线去抖)+总底池兜底;hero/公共牌reset 触发关闭")

        # 2026-06-06:桌规录入(SB/BB/ante)。提供则按【按钮+精确活跃集(card_marker)】确定性派
        # 强制注,绕开 OCR 盲注抖动(#230);未提供 → 回落 OCR _detect_blind_levels。
        self._table_blinds = self._read_table_blinds()
        if self._table_blinds:
            t = self._table_blinds
            logger.info(f"[桌规] SB={t['sb']} BB={t['bb']} ante={t['ante']} → 按钮+活跃集确定性派盲注/前注(免OCR)")
        # P0(2026-06-06):惰性盲注注入状态 + standing per-tick 活跃集。治"按钮切手超前于发牌→
        # 活跃集空→盲注漏派"(实测 ~19% 手)。开手挂 pending,每 tick 活跃集非空才派(等发牌完成)。
        # _active_set 整手 per-tick 维护(<1ms/tick),on-change emit = 顺带记 fold 转移(喂 P1)。
        self._blinds_pending = False
        self._blinds_attempts = 0
        self._active_set: set = set()
        self._az_state: dict = {}  # #243 第一步:per-seat all-in stack→0 影子检测态(每手 reset)
        self._showdown_latched = False  # #235 摊牌闸:本手是否已进结算(latch 后压制假弃牌;每手 reset)
        # P2a(2026-06-06):活跃集 silent-fold 救援状态。_hand_dealt_seats=本手见过牌的座,
        # _seat_gone_ticks=各座 card_marker 连续消失帧数(去抖)。补 fold_ocr 漏的弃牌。
        self._hand_dealt_seats: set = set()
        self._seat_gone_ticks: dict = {}

        self.tracker = StateTracker()
        self._labeler = LabelCapturer()  # 信源验证:旁路抽头目标信号(LABEL_SIGNAL 空→禁用)
        self._pot_debounce = {}          # pot 防抖游程({pending,count});换手 _start_new_hand 重置
        self._pot_label_latched = False  # 总底池颜色判定:本手是否已 latch 结算帧;每手 reset
        self._action_amt_wait = {}       # A(amount抓帧):每座"等金额settle"已跳帧数;记录/换手 reset
        self._AMT_SETTLE_MAX = 3         # 金额None最多等这么多帧(实测frame2即settle;此为永不settle的cap)
        self._action_blank_run = {}      # 吞街修:每座动作区连续空白帧数;≥2 清 prev 文本(详见 idle 路注释)
        self._hand_id_lock = {}          # ID手内冻结:本手 座位→名字 首用即锁(_hand_player_name);换手清
        self._allin_pending = {}         # all-in 写库分层:本手待手末过闸的 all_in 事件(seat→event);换手清
        # 总底池检测改【颜色】(2026-06-10):phash 因 8×8 下"总底池"汉字 vs 数字形状太像(inter=2)
        # 放弃;实测总底池=高饱和青字(白V>180像素=0)、数字=白(白V>180一堆)→ 颜色干净可分。
        # 先 shadow 量 white/teal 两计数(_pot_label_color)→ live 出分布再定阈,同 showdown_corner。

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

    def start(self, max_minutes: float | None = None):
        """Run the main capture loop.

        max_minutes: 到点自动停(挂机长局验收用),走与 Ctrl+C 相同的干净退出路径
        (当前 tick 跑完、_shutdown 落库)。None = 不限时(原行为)。
        """
        self.running = True
        deadline = (time.time() + max_minutes * 60.0) if max_minutes else None
        if deadline:
            logger.info("Pipeline started — capturing every %dms, auto-stop in %.0f min",
                        CAPTURE_INTERVAL_MS, max_minutes)
        else:
            logger.info("Pipeline started — capturing every %dms", CAPTURE_INTERVAL_MS)

        try:
            while self.running:
                self._tick()
                if deadline and time.time() >= deadline:
                    logger.info("Pipeline auto-stopped — max-minutes %.0f reached", max_minutes)
                    break
                time.sleep(CAPTURE_INTERVAL_MS / 1000.0)
        except KeyboardInterrupt:
            logger.info("Pipeline stopped by user")
        finally:
            self._shutdown()

    def stop(self):
        self.running = False

    # ── Tick ──────────────────────────────────────────────

    # T56(2026-05-29):全 phase 列表,每 tick 末按此 fill 0,
    # 保证 _phase_durations 各 list 同步长度.
    # T57(2026-05-29):新增 seat_* 子 phase(seat_actions 内拆出).
    _TICK_PHASES = (
        "capture_frame",  # 杠杆D.1:整窗一次性 grab(替 ~37 次逐区 grab)
        "capture_grab",   # A步1(2026-06-09):本 tick 所有逐ROI mss grab 累计耗时(钉死抓屏占比)
        "hero_capture", "hand_detect", "community", "community_reset",
        "pot", "active_set", "seat_actions", "showdown", "capture_ids",
        # T57 seat 子 phase
        "seat_stack_ocr", "seat_timer_ocr", "seat_fold_ocr",
        "seat_action_ocr", "seat_amount_ocr", "seat_avatar_hash",
        "seat_parse_persist",
        # 2026-06-01 spike A:定位 seat_actions 未计时 gap(~410ms)
        "seat_artifact",
        # 2026-06-05:hand 转换拆分(startup 卡)+ 整 seat 循环 + detect_empty/showdown
        "hand_end", "hand_start", "detect_empty", "showdown_cnn", "seat_loop",
    )

    def _tick(self):
        # T54(2026-05-29):tick 计时,末尾算 elapsed
        t_start = time.perf_counter()
        _grab0 = getattr(self.capturer, "grab_ms", 0.0)  # A步1:抓屏耗时起点快照,末尾算 delta
        rois = self.roi_manager.rois
        db = SessionLocal() if self._db_enabled else None

        # T52(2026-05-29):全局 tick 计数器递增,driving force-refresh 守护机制
        self.tracker._global_tick_counter += 1

        # T55(2026-05-29):db 累积耗时(commit + rollback + close).
        db_total_ms = 0.0
        # T56(2026-05-29):本 tick 各 phase 累积 ms.
        phase_ms: dict = {}
        # 2026-06-05:跨方法的细计时收集器(_detect_empty/showdown 等写这,末尾并入 phase_ms)。
        self._tick_extra_ms: dict = {}

        try:
            # 杠杆D.1:整窗抓一次进缓存,本 tick 所有 capture_roi 从缓存切片(替~37次独立grab)。
            if FRAME_CAPTURE:
                t_p = time.perf_counter()
                self.capturer.refresh_frame()
                phase_ms["capture_frame"] = (time.perf_counter() - t_p) * 1000.0

            # 1. Capture hero cards
            t_p = time.perf_counter()
            hero_1 = self.capturer.capture_roi(rois.hero_card_1)
            hero_2 = self.capturer.capture_roi(rois.hero_card_2)
            phase_ms["hero_capture"] = (time.perf_counter() - t_p) * 1000.0

            # 2. Detect new hand(2026-06-05:拆 hand_end / hand_start 计时,定位 startup 卡)
            t_p = time.perf_counter()
            button_cut = False
            if BUTTON_CUT:
                # step 2b:每 tick 白占比扫按钮 → 在线去抖+顺时针单调,确认 D 移座 = 换手(权威)。
                # 治公共牌 reset 过切(实测 btn=5 连两手=一手被劈两半,见 button-authority 切手)。
                btn_now, _ = self._scan_button_white_frac()
                button_cut, self._btn_confirmed, self._btn_pending, self._btn_pending_count = \
                    button_move_online(self._btn_confirmed, self._btn_pending,
                                       self._btn_pending_count, btn_now,
                                       num_seats=self.roi_manager.rois.num_seats)
                if button_cut:
                    logger.info(f"[step2b] 按钮移座 → seat {self._btn_confirmed},换手")
                    if self.tracker.has_active_hand:
                        _te = time.perf_counter()
                        self._end_current_hand(db)
                        self._tick_extra_ms["hand_end"] = (time.perf_counter() - _te) * 1000.0
                    _ts = time.perf_counter()
                    self._start_new_hand(db, hero_1, hero_2)
                    self._tick_extra_ms["hand_start"] = (time.perf_counter() - _ts) * 1000.0

            if not button_cut:
                if self.tracker.has_active_hand:
                    # hero 换牌触发:BUTTON_CUT 开时关闭(观战=按钮权威;hero ROI 是静态 chrome)
                    if not BUTTON_CUT and self.tracker.check_hero_cards(hero_1, hero_2):
                        _te = time.perf_counter()
                        self._end_current_hand(db)
                        self._tick_extra_ms["hand_end"] = (time.perf_counter() - _te) * 1000.0
                        _ts = time.perf_counter()
                        self._start_new_hand(db, hero_1, hero_2)
                        self._tick_extra_ms["hand_start"] = (time.perf_counter() - _ts) * 1000.0
                else:
                    # 无活跃手 → 启动首手(两模式都需起点;BUTTON_CUT 下首手由此起,之后靠按钮切)
                    if self._hero_cards_present(hero_1, hero_2):
                        _ts = time.perf_counter()
                        self._start_new_hand(db, hero_1, hero_2)
                        self._tick_extra_ms["hand_start"] = (time.perf_counter() - _ts) * 1000.0
            phase_ms["hand_detect"] = (time.perf_counter() - t_p) * 1000.0

            # 3. Community cards
            if self.tracker.has_active_hand:
                t_p = time.perf_counter()
                self._process_community_cards(db, rois)
                phase_ms["community"] = (time.perf_counter() - t_p) * 1000.0

            # 3b. Observer-mode hand-start fallback: if hero cards are not
            # available (default ROI = stable browser chrome, never changes),
            # use community count drop from > 0 to 0 as the new-hand signal.
            # BUTTON_CUT 开时【关闭】此触发(它是过切元凶:公共牌假 reset 把一手劈多手);
            # 换手交按钮权威,"总底池"标签(_process_pot)仍作结算兜底。
            if self.tracker.has_active_hand and self.tracker.community_just_reset() and not BUTTON_CUT:
                t_p = time.perf_counter()
                logger.info("Community reset detected → starting new hand (observer mode)")
                self._end_current_hand(db)
                self._start_new_hand(db, hero_1, hero_2)
                phase_ms["community_reset"] = (time.perf_counter() - t_p) * 1000.0

            # 4. Pot size — runs BEFORE seat actions so that _process_seat_actions
            # has access to both pot_before (saved on tracker._pot_before_tick)
            # and pot_after (= tracker.latest_pot_bb) for cross-validation raw_data.
            if self.tracker.has_active_hand:
                t_p = time.perf_counter()
                self._process_pot(db, rois)
                phase_ms["pot"] = (time.perf_counter() - t_p) * 1000.0

            # P0(2026-06-06):standing per-tick 活跃集 + 惰性盲注注入(桌规模式)。
            # 必须在 _process_seat_actions 之前(否则本 tick 真实动作先于合成 POST 入库,seq 乱)。
            if self._table_blinds and self.tracker.has_active_hand:
                t_p = time.perf_counter()
                self._tick_active_set_and_blinds(db)
                phase_ms["active_set"] = (time.perf_counter() - t_p) * 1000.0

            # #241(rebuy 前置):每 tick 扫 +xx(瞬态,某 tick 见到即 latch 该座这手"合法进账")。
            # 两道约束(治误报):① 跳每手前 _xx_deal_skip tick(发牌窗,牌背飞入s0)
            # ② 只扫活跃集座(只有牌里的人能赢 + 活跃集发言概率更低)。活跃集空则回退全座。
            # 2026-06-11 常开(原 _empty_refs 闸 = 只有 #241 rebuy 消费它的年代;现 hands.result
            # 赢家归属也吃它 → 133 手 result 全 null 的直接原因就是这道闸)。成本=活跃座黄像素
            # 计数(便宜)+ 命中时 digit 读;验收时看 tick_stats 的 win_scan 项确认无回归。
            if self.tracker.has_active_hand and \
                    (self.tracker._global_tick_counter - self._hand_start_tick) >= self._xx_deal_skip:
                _tw = time.perf_counter()
                self._scan_win_amount(self._active_set or None)
                self._tick_extra_ms["win_scan"] = (time.perf_counter() - _tw) * 1000.0

            # 5. Seat actions — writes raw_data with stack_delta + pot_delta evidence
            if self.tracker.has_active_hand:
                t_p = time.perf_counter()
                self._process_seat_actions(db, rois)
                phase_ms["seat_actions"] = (time.perf_counter() - t_p) * 1000.0
                # T57(2026-05-29):seat 内子 phase merge 入 phase_ms.
                for sub_name, sub_val in self.tracker._seat_subphase_ms.items():
                    phase_ms[sub_name] = sub_val

            # 6. Live showdown capture — must happen DURING river phase while overlay
            # is visible.  Old architecture grabbed at hand-end (after community reset)
            # → overlay gone → caught only avatar pixels.  See 2026-05-26 diff diagnosis.
            if self.tracker.has_active_hand:
                t_p = time.perf_counter()
                self._try_capture_showdown_live(rois)
                phase_ms["showdown"] = (time.perf_counter() - t_p) * 1000.0

            # 7. T42(2026-05-29):TempUser-cached seat 周期性重试 _capture_player_ids
            # 旧逻辑只在 _start_new_hand 触发,而 hand-start 那瞬间上一手 action
            # overlay 还没散(WePoker UI ~500ms 淡出)→ id ROI 永远抓到 "跟注"
            # → 永远 TempUser。每 8 tick(2s)重试,T41 cache lock 让真名 seat
            # 零开销跳过,只有 TempUser-cached seat 真重 OCR,捡 action 间隙的
            # 干净帧。一旦干净 → T41 _upgrade_tempuser_to_real 触发 DB sync。
            if self.tracker.has_active_hand:
                self._capture_ids_retry_counter = getattr(
                    self, "_capture_ids_retry_counter", 0) + 1
                if self._capture_ids_retry_counter % 8 == 0:
                    if any(n.startswith("TempUser_")
                           for n in self.tracker.player_id_map.values()):
                        t_p = time.perf_counter()
                        self._capture_player_ids()
                        phase_ms["capture_ids"] = (time.perf_counter() - t_p) * 1000.0

            if db is not None:
                db_t = time.perf_counter()
                db.commit()
                db_total_ms += (time.perf_counter() - db_t) * 1000.0
        except Exception:
            logger.error("Tick failed", exc_info=True)
            if db is not None:
                db_t = time.perf_counter()
                db.rollback()
                db_total_ms += (time.perf_counter() - db_t) * 1000.0
        finally:
            if db is not None:
                db_t = time.perf_counter()
                db.close()
                db_total_ms += (time.perf_counter() - db_t) * 1000.0

        # 2026-06-05:并入跨方法细计时(hand_end/hand_start/detect_empty/showdown_cnn 等)。
        phase_ms.update(self._tick_extra_ms)
        # A步1(2026-06-09):本 tick 逐ROI抓屏累计 = delta(贯穿所有 capture_roi/capture_raw 调用点)。
        phase_ms["capture_grab"] = getattr(self.capturer, "grab_ms", 0.0) - _grab0
        # T56(2026-05-29):未执行的 phase 补 0,确保 batch 同步长度.
        for name in self._TICK_PHASES:
            self.tracker._phase_durations.setdefault(name, []).append(
                phase_ms.get(name, 0.0))

        # T54(2026-05-29):tick 耗时统计,每 20 tick 输出 stats + emit diag.
        # 用途:实测 T52 后真 tick 时间(我之前推估 100-900ms 全凭印象),
        # 再决定是否降 CAPTURE_INTERVAL_MS sleep.
        tick_ms = (time.perf_counter() - t_start) * 1000.0
        self.tracker._tick_durations.append(tick_ms)
        # T55(2026-05-29):db 拆分.
        self.tracker._db_durations.append(db_total_ms)
        if len(self.tracker._tick_durations) >= 20:
            durs = sorted(self.tracker._tick_durations)
            db_durs = sorted(self.tracker._db_durations)
            n = len(durs)
            tick_avg = sum(durs) / n
            db_avg = sum(db_durs) / n
            # T56(2026-05-29):per-phase stats.
            phases_stats = {}
            for name in self._TICK_PHASES:
                p_durs = self.tracker._phase_durations.get(name, [])
                if not p_durs:
                    continue
                p_sorted = sorted(p_durs)
                p_avg = sum(p_sorted) / len(p_sorted)
                # exec_count = 非 0 次数(衡量 phase 触发率)
                exec_count = sum(1 for v in p_durs if v > 0)
                phases_stats[name] = {
                    "avg_ms": round(p_avg, 1),
                    "max_ms": round(p_sorted[-1], 1),
                    "exec_count": exec_count,
                    "pct_of_tick": round(100.0 * p_avg / tick_avg, 1) if tick_avg > 0 else 0.0,
                }
            stats = {
                "n": n,
                "min_ms": round(durs[0], 1),
                "median_ms": round(durs[n // 2], 1),
                "p95_ms": round(durs[int(n * 0.95)], 1),
                "max_ms": round(durs[-1], 1),
                "avg_ms": round(tick_avg, 1),
                "sleep_ms": CAPTURE_INTERVAL_MS,
                "effective_hz": round(1000.0 / (tick_avg + CAPTURE_INTERVAL_MS), 2),
                # T55:db 拆分.
                "db_avg_ms": round(db_avg, 1),
                "db_median_ms": round(db_durs[n // 2], 1),
                "db_p95_ms": round(db_durs[int(n * 0.95)], 1),
                "db_max_ms": round(db_durs[-1], 1),
                "db_pct_of_tick": round(100.0 * db_avg / tick_avg, 1) if tick_avg > 0 else 0.0,
                # T56:phase 拆分.
                "phases": phases_stats,
            }
            # T56:取 pct_of_tick top 3 phase 出 log,避免 log 行过长.
            top_phases = sorted(
                phases_stats.items(),
                key=lambda kv: kv[1]["pct_of_tick"],
                reverse=True,
            )[:3]
            top_str = " ".join(
                f"{n_}={v['avg_ms']}ms({v['pct_of_tick']}%)" for n_, v in top_phases
            )
            logger.info(
                f"[tick stats] n={stats['n']} min={stats['min_ms']}ms "
                f"median={stats['median_ms']}ms p95={stats['p95_ms']}ms "
                f"max={stats['max_ms']}ms avg={stats['avg_ms']}ms "
                f"sleep={stats['sleep_ms']}ms → {stats['effective_hz']}Hz | "
                f"db_avg={stats['db_avg_ms']}ms db_p95={stats['db_p95_ms']}ms "
                f"db_max={stats['db_max_ms']}ms ({stats['db_pct_of_tick']}% of tick) | "
                f"top phases: {top_str}"
            )
            diag.emit(
                "pipeline.tick_stats",
                stats,
                hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None,
            )
            self.tracker._tick_durations.clear()
            self.tracker._db_durations.clear()
            self.tracker._phase_durations.clear()

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
        self._az_state = {}  # #243:新手清 all-in stack→0 影子检测态(每手一座只 emit 一次)
        self._showdown_latched = False  # #235:新手清摊牌闸 latch
        self._pot_debounce = {}  # 新手清 pot 防抖游程(新手底池从小重起,别和上手游程串)
        self._pot_label_latched = False  # 新手清总底池结算 latch(每手一个结算帧)
        self._action_amt_wait = {}  # 新手清"等金额settle"跳帧计数
        self._action_blank_run = {}  # 新手清动作区空白游程(prev 文本本身由 detector reset 清)
        self._hand_id_lock = {}  # 新手清 ID 冻结(换人合法发生在手间,新手重新首用锁定)
        self._allin_pending = {}  # 新手清 all-in 待写队列(上手未 flush 的此刻已无效)
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

        # T17(2026-05-28):抓 SB/BB 强制下注金额 → blind_level
        # 依赖 button_seat_index 准确(T13 fix 后 ✅)
        # 2026-06-06:桌规已录入 → 盲注值权威来自用户输入,跳过 OCR(治 #230 抖动);_inject 走桌规路径。
        if not self._table_blinds:
            self._detect_blind_levels()

        # Capture each seat's platform user-ID before any in-hand action obscures it
        self._capture_player_ids()

        # 12a-pre: snapshot per-seat stack at hand start (before any betting).
        # Stored on hand.raw_data so insurance / rake / stack-delta validation can
        # back-compute on a hand basis later.
        initial_stacks = self._capture_seat_stacks()

        # #241(rebuy 前置):hand-start 每座占用判定(空桌基线二元)。存 raw_data + 打印每区
        # hamming 供 live 调阈(空 vs 占位的真实间隔只有占位帧才看得到)。_empty_refs 缺则跳过。
        occupancy = self._classify_occupancy()
        self._hand_win_seats = set()  # #241:重置上一手 +xx latch
        self._hand_win_amounts = {}   # 重置上一手 +xx 金额 latch
        self._hand_start_tick = self.tracker._global_tick_counter  # #241:记本手起点,+xx 跳发牌窗

        # 摊牌 baseline 强初始化 — 之前 baseline 只在 fold_area OCR 为空时更新,
        # 但高活跃玩家很少有 idle empty tick → baseline 永不建立 → 摊牌 skip。
        # 在 hand 起始时(无 overlay 状态)强制建一次 baseline。
        self._initialize_avatar_baselines()

        # T46-A(2026-05-29):hand-start 扫描空座(全 0 phash + stack 无数字
        # 双确认)→ add to _empty_seats → action loop 顶部统一 skip,跟 fold 同治。
        _de = time.perf_counter()
        self._detect_empty_seats()
        if hasattr(self, "_tick_extra_ms"):
            self._tick_extra_ms["detect_empty"] = (time.perf_counter() - _de) * 1000.0

        # T48 v3(2026-05-29):指针状态机 hand-start init.
        # UTG = (button + 3) % num_seats(preflop 第一个行动玩家).
        # button 不可知时(button OCR 失败)pointer 留 None,等 timer 出现纠正.
        button = (self.tracker.current_hand.raw_data or {}).get("button_seat_index")
        if button is not None:
            num_seats = self.roi_manager.rois.num_seats
            utg = (button + 3) % num_seats
            self.tracker._pointer_state["current_seat"] = utg
            self.tracker._pointer_state["street"] = "preflop"
            diag.emit(
                "pointer.hand_init",
                {"button_seat": button, "init_pointer_seat": utg,
                 "street": "preflop", "num_seats": num_seats},
                hand_id=self.tracker.current_hand.id,
            )

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
        if occupancy:
            hand.raw_data["seat_occupancy"] = {
                str(s): v["occupied"] for s, v in occupancy.items() if v["occupied"] is not None}
            # live 调阈用:每座 occupied? + 各区到空桌基线的 hamming(占位应远>阈,空应≈0)
            _occ_log = "  ".join(
                f"s{s}={'占' if v['occupied'] else ('空' if v['occupied'] is False else '?')}"
                f"({','.join(f'{r}{h}' for r, h in v['ham'].items())})"
                for s, v in sorted(occupancy.items()))
            logger.info(f"[占用] 阈{self._occupancy_th} {_occ_log}")

        if db is not None and (seats_map or initial_stacks):
            # Update DB hand with seats metadata + initial stacks (best-effort)
            try:
                self.hand_repo.update(db, hand)
            except Exception:
                logger.warning(f"Hand {hand.id} hand-start metadata update failed", exc_info=True)
        logger.info(f"Hand {hand.id} — hero: {hand.hero_cards} — ids: {self.tracker.player_id_map}")

        # T65(2026-05-29):POST_SB / POST_BB synthetic 注入.
        # WePoker UI 不显示 POST overlay → 没事件 → SB/BB 玩家 PF voluntary 系统偏低.
        # hand-start 在 button + blind_level + player_id 全到位后,注入 2 个 synthetic events
        # (seq=1,2),让 SB/BB 玩家"forced 行动"在数据层显式存在.
        # action_type=POST_*,被 stats SQL whitelist 排除,不算 VPIP.
        # raw_data.synthetic=True marker 让 dashboard / Path B 区分 真 OCR vs 合成.
        # P0(2026-06-06):桌规模式 → 不在此内联注入(此刻发牌可能未完成→活跃集空→漏派),
        # 改挂 _blinds_pending,由 _tick 每 tick 检查、活跃集非空(发牌完成)才派;重置 per-tick 活跃集。
        if self._table_blinds:
            self._blinds_pending = True
            self._blinds_attempts = 0
            self._active_set = set()
            self._hand_dealt_seats = set()
            self._seat_gone_ticks = {}
        else:
            self._inject_post_events(db)   # OCR 路径:hand-start 内联(community 未发时盲注 ROI 准)

    def _hand_player_name(self, sidx, default=None):
        """ID 手内冻结(2026-06-11 验尸):133手审计 24 个洞里 15 个=同手同座名字漂移
        (fold_area 被 overlay 污染→头像hash连续发散→evict→重认到别名,'11191↔好好千他们'
        一对就 7 手)。换人只发生在手与手之间 → 本手事件归属【首用即锁】:首次取到真名后,
        本手内一律用锁定名,_capture_player_ids 的中途改名只影响下一手。
        占位名(Player_/TempUser_)不锁:TempUser→真名升级有 _upgrade_tempuser_to_real
        的 DB 同步兜底,锁死反而阻断升级。"""
        live = self.tracker.player_id_map.get(sidx, default)
        locked = self._hand_id_lock.get(sidx)
        if locked is not None:
            return locked
        if live is not None and not str(live).startswith(("Player_", "TempUser_")):
            self._hand_id_lock[sidx] = live
        return live

    def _seat_position(self, seat_idx):
        """seat → Position(取 tracker 位置映射,兜底 BTN;ante 的 position 仅元数据)。"""
        from events.models import Position
        pv = self.tracker._position_map.get(seat_idx)
        if pv:
            try:
                return Position(pv)
            except ValueError:
                pass
        return Position.BTN

    def _emit_forced_event(self, db, hand, seat_idx, action_type, amount, position):
        """造 1 条 synthetic 强制注 event(POST_SB/BB/ANTE)+ 持久化。玩家未知则跳(不伪造名污染画像)。"""
        if self.tracker.is_skippable_seat(seat_idx):
            return
        player_name = self._hand_player_name(seat_idx)  # ID 手内冻结:盲注与动作同名源
        if not player_name:
            diag.emit("post.injection_skipped",
                      {"reason": "player_unknown", "seat": seat_idx, "action": action_type.value},
                      hand_id=hand.id)
            return
        stack_before = self.tracker._prev_stack.get(seat_idx)
        stack_after = (stack_before - amount) if stack_before is not None else None
        event = self.tracker.normalizer.create_event(
            hand=hand, player_name=player_name, position=position,
            action_type=action_type, amount=amount, facing_action=None)
        event.confidence_score = 0.95
        event.raw_data = {
            "seat_index": seat_idx,   # 座位主键(2026-06-13):对账层按座取端点,免玩家名反推(治 MAPPING_GAP)
            "synthetic": True, "source": "table_blind_injection",
            "stack_before": stack_before, "stack_after": stack_after,
            "stack_delta": amount, "blind_level_source": "table_input",
        }
        if db is not None:
            try:
                self.event_repo.create(db, event)
            except Exception:
                logger.warning(f"forced inject seat_{seat_idx} {action_type.value} failed", exc_info=True)
                return
        diag.emit("post.injection_done",
                  {"seat": seat_idx, "player": player_name, "action": action_type.value,
                   "amount": amount, "source": "table_input"}, hand_id=hand.id)
        # A′(2026-06-11):盲注播种本街金额地板 — SB/BB 投入即本街显示下限,供 amount settle 闸
        # 识别"动作首帧读到旧盲注值"(live 实测 SB call 记 20=小盲 10/11、BB raise 记 40=大盲 5/5)。
        # A″:同时播种该座自身投入(SB 读回 2、BB 读回 4 即"读到自己旧显示")。
        if action_type in (ActionType.POST_SB, ActionType.POST_BB):
            self.tracker._street_amt_max = max(self.tracker._street_amt_max, amount)
            self.tracker._seat_street_amt[seat_idx] = max(
                self.tracker._seat_street_amt.get(seat_idx, 0.0), amount)

    def _tick_active_set_and_blinds(self, db):
        """P0(2026-06-06)每 tick:读精确活跃集(standing per-tick 信号)+ on-change 诊断(记 fold
        转移,喂 P1)+ 惰性盲注注入(发牌完成=活跃集非空才派,治按钮切手超前发牌的 ~19% 漏派)。"""
        active = self._detect_active_set(emit=False)
        # #235 摊牌闸 latch:未弃座亮白角 = 对抗者揭牌 → 本手进结算(已弃座白角=主动亮牌,不算 →
        # 治"弃后主动亮牌"边界;白角无条件检 → 治"剔活跃集就不看"race)。latch 后压制结算期假弃牌。
        if SHOWDOWN_GATE and not self._showdown_latched and self.tracker.current_hand is not None:
            reveal = self._detect_showdown_corner() - self.tracker._folded_seats
            if reveal:
                self._showdown_latched = True
                diag.emit("showdown.latched", {"seats": sorted(reveal), "by": "showdown_corner"},
                          hand_id=self.tracker.current_hand.id)
        # on-change 诊断:成员变才记(入场/弃牌/手末)→ 天然就是 fold 转移记录,不每 tick 刷库
        if active != self._active_set:
            cc = (self.tracker.current_hand.community_cards or {}) if self.tracker.current_hand else {}
            street = ("river" if cc.get("river") else "turn" if cc.get("turn")
                      else "flop" if cc.get("flop") else "preflop")
            diag.emit("active_set.changed",
                      {"prev": sorted(self._active_set), "now": sorted(active),
                       "left": sorted(self._active_set - active),
                       "joined": sorted(active - self._active_set), "street": street},
                      hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None)
            self._active_set = active

        # P2a(2026-06-06):活跃集 silent-fold 救援——补 fold_ocr 漏的弃牌(P1:活跃集召回严格>fold_ocr)。
        # 安全收口:① 仅 preflop/flop/turn(river 摊牌亮牌会让 card_marker 消失→留 2b 加护栏);
        # ② 去抖 2 帧(灭 P1 的 0.6% 闪读);③ 已被 fold_ocr 标过的不重复(标 _folded_seats 后
        #   is_skippable 让 _process_seat_actions 跳过该座 → 不双发 FOLD);④ fold_ocr 全留不删。
        if self.tracker.current_hand is not None:
            cc = self.tracker.current_hand.community_cards or {}
            cur_street = ("river" if cc.get("river") else "turn" if cc.get("turn")
                          else "flop" if cc.get("flop") else "preflop")
            self._hand_dealt_seats |= active   # 本手见过牌的座(累积)
            for s in list(self._hand_dealt_seats):
                if s in active:
                    self._seat_gone_ticks[s] = 0
                    continue
                self._seat_gone_ticks[s] = self._seat_gone_ticks.get(s, 0) + 1
                if (cur_street != "river" and self._seat_gone_ticks[s] >= 2
                        and s not in self.tracker._folded_seats
                        and not (SHOWDOWN_GATE and self._showdown_latched)  # #235:摊牌后 card_marker 消失=亮牌非弃牌,不补
                        and not self._pot_label_latched):  # 幽灵fold修(2026-06-11 审计≥31例):总底池
                    # latch=进结算(132/133 手可靠),结算期全桌 card_marker 消失是收牌动画,
                    # 不是弃牌 — 此前这些被补成"赢家/全下者弃牌"污染归属。latch 后一律不补。
                    self._rescue_silent_fold(db, s, cur_street)
        # 惰性盲注注入:等发牌完成(活跃非空)才派;非空但定不出 SB/BB 也不再重试(那是按钮/活跃问题非时机)
        if self._blinds_pending:
            self._blinds_attempts += 1
            if active:
                self._blinds_pending = False
                self._inject_forced_from_blinds(db, active=active)
            elif self._blinds_attempts >= 12:   # ~超时(发牌迟迟不检出=异常)→ 放弃,免无限挂
                self._blinds_pending = False
                diag.emit("post.injection_skipped",
                          {"reason": "active_empty_timeout", "attempts": self._blinds_attempts},
                          hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None)
                logger.warning("[桌规派注] 活跃集持续为空(发牌未检出)→ 超时放弃本手盲注注入")

    def _rescue_silent_fold(self, db, seat, street):
        """P2a:活跃集确认某座 card_marker 持续消失(非 river,去抖过)= silent fold,fold_ocr 漏读 → 补。
        标 _folded_seats(→该座后续被 skip)+ 合成 FOLD action_event(玩家已知才落,不伪造名)+ 诊断。
        与 fold_ocr 互补:这里只补 fold_ocr 没标过的(去重见 caller)。"""
        self.tracker._folded_seats.add(seat)
        hand = self.tracker.current_hand
        player_name = self._hand_player_name(seat)  # ID 手内冻结:救援 fold 与动作同名源
        diag.emit("fold.activeset_rescue",
                  {"seat": seat, "street": street, "player": player_name},
                  hand_id=hand.id if hand else None)
        logger.info(f"[P2a fold救援] seat_{seat} card_marker 持续消失({street})= silent fold,fold_ocr 漏读 → 补")
        if player_name and hand is not None and db is not None:
            try:
                event = self.tracker.normalizer.create_event(
                    hand=hand, player_name=player_name, position=self._seat_position(seat),
                    action_type=ActionType.FOLD, amount=None, facing_action=None)
                event.confidence_score = 0.9
                event.raw_data = {"seat_index": seat, "synthetic": True,
                                  "source": "activeset_fold_rescue", "street": street}
                self.event_repo.create(db, event)
            except Exception:
                logger.warning(f"activeset fold rescue event seat_{seat} failed", exc_info=True)

    def _inject_forced_from_blinds(self, db, active=None):
        """桌规录入路径(2026-06-06):按钮 + 精确活跃集(card_marker)→ `blinds_from_button` 定
        SB/BB 座(走活跃集下一/下下位,跳空座),ante 给全活跃座。用户输入值,**不读 OCR 盲注**。
        active 可由 per-tick 惰性注入传入(已读,免重复);None 则现读。"""
        from events.models import Position
        hand = self.tracker.current_hand
        raw = hand.raw_data or {}
        button = raw.get("button_seat_index")
        num_seats = self.roi_manager.rois.num_seats
        if active is None:
            active = self._detect_active_set()
        sb_seat, bb_seat = blinds_from_button(button, active, num_seats)
        tb = self._table_blinds
        sb_amt, bb_amt, ante = tb["sb"], tb["bb"], tb["ante"]
        hand.raw_data = {**raw,
                         "blind_level": {"sb": sb_amt, "bb": bb_amt, "ante": ante},
                         "blind_level_source": "table_input",
                         "active_set": sorted(active), "sb_seat": sb_seat, "bb_seat": bb_seat}
        if sb_seat is None or bb_seat is None:
            diag.emit("post.injection_skipped",
                      {"reason": "blinds_from_button_none", "button": button,
                       "active": sorted(active), "num_seats": num_seats}, hand_id=hand.id)
            logger.warning(f"[桌规派注] 活跃集 {sorted(active)} / 按钮 {button} → 定不出 SB/BB,跳过")
            return
        logger.info(f"[桌规派注] 活跃集{sorted(active)} 按钮{button} → SB=s{sb_seat}({sb_amt}) "
                    f"BB=s{bb_seat}({bb_amt}) ante={ante}×{len(active)}座")
        # ante 给全活跃座 + SB/BB 给算出的座(分条 POST event,与 reconstruct forced 分开累加一致)
        plan = []
        if ante and ante > 0:
            for s in sorted(active):
                plan.append((s, ActionType.POST_ANTE, ante, self._seat_position(s)))
        plan.append((sb_seat, ActionType.POST_SB, sb_amt, Position.SB))
        plan.append((bb_seat, ActionType.POST_BB, bb_amt, Position.BB))
        for seat_idx, atype, amount, pos in plan:
            self._emit_forced_event(db, hand, seat_idx, atype, amount, pos)

    def _inject_post_events(self, db):
        """T65:hand-start 注入 SB/BB POST events,seq=1,2,synthetic=True.

        skip 条件(任一命中):
          - button_seat_index None(button OCR 失败)
          - blind_level.sb / .bb None(blind OCR 失败)
          - SB/BB seat ∈ _empty_seats
          - player_id_map[seat] is None
          - num_seats < 3(heads-up 规则不同,留 backlog)
        """
        hand = self.tracker.current_hand
        if hand is None:
            return
        # 2026-06-06:桌规已录入 → 按钮+精确活跃集确定性派强制注(免 OCR,治 #230);否则走下方 OCR 路径。
        if self._table_blinds:
            self._inject_forced_from_blinds(db)
            return
        raw = hand.raw_data or {}
        button = raw.get("button_seat_index")
        blind = raw.get("blind_level") or {}
        sb_amount = blind.get("sb")
        bb_amount = blind.get("bb")
        num_seats = self.roi_manager.rois.num_seats

        skip_reason = None
        if button is None:
            skip_reason = "button_seat_index_none"
        elif sb_amount is None or bb_amount is None:
            skip_reason = "blind_level_missing"
        elif num_seats < 3:
            skip_reason = "heads_up_unsupported"

        if skip_reason:
            diag.emit(
                "post.injection_skipped",
                {"reason": skip_reason,
                 "button": button, "blind": blind, "num_seats": num_seats},
                hand_id=hand.id,
            )
            return

        sb_seat = (button + 1) % num_seats
        bb_seat = (button + 2) % num_seats

        # per-seat skip + inject
        for seat_idx, action_type, amount, pos in (
            (sb_seat, ActionType.POST_SB, sb_amount, Position.SB),
            (bb_seat, ActionType.POST_BB, bb_amount, Position.BB),
        ):
            # fold/empty 座 → 跳过
            if self.tracker.is_skippable_seat(seat_idx):
                diag.emit(
                    "post.injection_skipped",
                    {"reason": "seat_empty", "seat": seat_idx,
                     "action": action_type.value},
                    hand_id=hand.id,
                )
                continue
            player_name = self.tracker.player_id_map.get(seat_idx)
            if not player_name:
                diag.emit(
                    "post.injection_skipped",
                    {"reason": "player_unknown", "seat": seat_idx,
                     "action": action_type.value},
                    hand_id=hand.id,
                )
                continue

            stack_before = self.tracker._prev_stack.get(seat_idx)
            stack_after = (stack_before - amount) if stack_before is not None else None

            event = self.tracker.normalizer.create_event(
                hand=hand,
                player_name=player_name,
                position=pos,
                action_type=action_type,
                amount=amount,
                facing_action=None,
            )
            event.confidence_score = 0.9
            event.raw_data = {
                "seat_index": seat_idx,   # 座位主键(2026-06-13):同上,对账按座取端点
                "synthetic": True,
                "source": "post_injection",
                "stack_before": stack_before,
                "stack_after": stack_after,
                "stack_delta": amount,
                "pot_before": 0 if action_type == ActionType.POST_SB else sb_amount,
                "pot_after": (sb_amount if action_type == ActionType.POST_SB
                              else sb_amount + bb_amount),
                "pot_delta": amount,
                "blind_level_source": "ocr",
            }
            if db is not None:
                try:
                    self.event_repo.create(db, event)
                except Exception:
                    logger.warning(
                        f"POST injection seat_{seat_idx} {action_type.value} "
                        f"failed", exc_info=True)
                    continue
            diag.emit(
                "post.injection_done",
                {"seat": seat_idx, "player": player_name,
                 "action": action_type.value, "amount": amount},
                hand_id=hand.id,
            )

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
            # significantly from cached hash → player changed seat, release cache.
            # 2026-06-08 去抖(治 churn):fold_area 在手内被牌/动作/弃牌/timer overlay 污染 →
            # 头像 hash 单帧飘 → 假 swap → evict → 重读 → 名字churn(soak 实测 16 TempUser+36名)。
            # 真换人【持续】发散、overlay【瞬态】→ 要求连续 ≥_avatar_swap_min 次才 evict。
            self._avatar_swap_count = getattr(self, "_avatar_swap_count", {})
            _swap_min = int(os.getenv("POKEMIR_AVATAR_SWAP_MIN", "2"))
            if avatar_hash and seat.seat_index in self.tracker.player_id_map:
                cached_name = self.tracker.player_id_map[seat.seat_index]
                cached_hash = next(
                    (h for h, n in self.tracker._avatar_fingerprints.items() if n == cached_name),
                    None,
                )
                if cached_hash and _hamming(avatar_hash, cached_hash) > 12:
                    _cnt = self._avatar_swap_count.get(seat.seat_index, 0) + 1
                    self._avatar_swap_count[seat.seat_index] = _cnt
                    if _cnt >= _swap_min:  # 连续发散达阈 → 真换人,evict
                        logger.info(f"_capture_player_ids: seat_{seat.seat_index} avatar swap "
                                    f"(hamming {_hamming(avatar_hash, cached_hash)}, 连续{_cnt}次), "
                                    f"unlocking {cached_name!r}")
                        del self.tracker.player_id_map[seat.seat_index]
                        self._avatar_swap_count[seat.seat_index] = 0
                elif cached_hash:
                    self._avatar_swap_count[seat.seat_index] = 0  # 匹配 → 重置去抖(瞬态 overlay 不累积)

            # #2 Cache lock: don't re-OCR a seat we already have a valid name for.
            # T41(2026-05-29):EXCEPTION — TempUser_xxx 是占位 fallback,允许 re-OCR
            # 拿到真名再升级,而不是永久锁死(原 bug:首次遇 action_text_contamination
            # 就 TempUser 锁,真名永远进不来)。
            cached_name_existing = self.tracker.player_id_map.get(seat.seat_index)
            if cached_name_existing and not cached_name_existing.startswith("TempUser_"):
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
                # T43(2026-05-29):匹到 TempUser_xxx 占位 → 不能 short-circuit,
                # 必须 fall through 到 OCR retry,否则 TempUser→匹到自己→assign
                # 同 TempUser→continue 自循环锁死,永远不触发 T41 升级
                if best_match and best_dist <= 6 and not best_match.startswith("TempUser_"):
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
            # #239 垃圾名护栏(2026-06-11 审计):纯数字串/超短串 = OCR 把筹码数字、残缺笔画
            # 当名字读('11191' 系一串筹码值,作为别名吸走真名 7 手;'111'/'0260'/'4111' 同源)。
            # 不注册、不进下方模糊合并(否则越合并越毒),走 avatar TempUser fallback 保身份连续。
            # 注意:含数字的真名(KV095/加加288)合法 → 只拒【纯】数字和 len<2,不拒混合。
            if text.isdigit() or len(text) < 2:
                if avatar_hash and avatar_hash not in self.tracker._avatar_fingerprints:
                    temp_name = f"TempUser_{avatar_hash[:8]}"
                    self.tracker.player_id_map[seat.seat_index] = temp_name
                    self.tracker._avatar_fingerprints[avatar_hash] = temp_name
                    diag.emit("player.tempuser_assigned",
                              {"seat": seat.seat_index, "hash_prefix": avatar_hash[:8],
                               "temp_name": temp_name, "reason": "garbage_name_rejected",
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
            # T41(2026-05-29):TempUser → 真名升级时 DB sync,避免历史 action_events 残留旧 TempUser
            if (cached_name_existing
                    and cached_name_existing.startswith("TempUser_")
                    and cached_name_existing != text):
                self._upgrade_tempuser_to_real(seat.seat_index, cached_name_existing, text)
            self.tracker.player_id_map[seat.seat_index] = text
            # #4 Register avatar fingerprint for future lookup
            if avatar_hash:
                self.tracker._avatar_fingerprints[avatar_hash] = text

    def _end_current_hand(self, db):
        # 12a-pre: snapshot per-seat stack BEFORE finalize, while tracker.current_hand
        # is still valid. Stored on hand.raw_data for insurance / rake validation.
        final_stacks = self._capture_seat_stacks()
        # all-in 写库闸:手末统一 flush(此刻 broken 看护已定稿;hand 仍有效,事件带原 seq)
        self._flush_pending_allins(db)
        cur = self.tracker.current_hand
        if cur is not None:
            if cur.raw_data is None:
                cur.raw_data = {}
            cur.raw_data["player_stacks_final"] = final_stacks
            # 座位→名字快照(2026-06-13):对账层按座位主键,名字仅展示/跨手画像锚定时贴
            # (player_id_map = {seat_idx: name};名字读错/漂移只毒画像,不再毒对账)
            cur.raw_data["seat_names"] = {int(k): v for k, v in self.tracker.player_id_map.items()}
            if self._empty_refs:  # #241:本手 +xx latch(合法进账座 = 赢/边池/保险),喂 rebuy 排除
                cur.raw_data["win_phash_seats"] = sorted(self._hand_win_seats)
                logger.info(f"[+xx] 本手合法进账座(黄数量阈{self._win_yellow_count}): {sorted(self._hand_win_seats) or '无'}")
            # #226(2026-06-06):端点筹码级重建——每手 per-seat 净额/赢家/rake(可靠桩,
            # per-action 噪声不影响)。存 raw_data 供画像/复盘;纯逻辑见 reconstruct_hand_chips。
            try:
                _init = {int(k): v for k, v in (cur.raw_data.get("player_stacks_initial") or {}).items()}
                _final = {int(k): v for k, v in final_stacks.items()}
                _bl = cur.raw_data.get("blind_level") or {}
                _recon = reconstruct_hand_chips(
                    _init, _final, pot=self.tracker.latest_pot_bb,
                    sb=_bl.get("sb") or 0, bb=_bl.get("bb") or 0, ante=_bl.get("ante") or 0)
                cur.raw_data["chip_reconstruction"] = _recon
                if _recon["winners"]:
                    logger.info(f"[#226重建] 赢家 seat{_recon['winners']} "
                                f"净{[_recon['net'][w] for w in _recon['winners']]} rake{_recon['rake']} "
                                f"{'⚠ '+';'.join(_recon['flags']) if _recon['flags'] else 'OK'}")
            except Exception:
                logger.warning("chip_reconstruction failed", exc_info=True)
            # #12a Insurance inference from stack pattern (用户假说):
            #   went-all-in players whose stack_final is non-zero AND non-round
            #   = likely bought insurance (payout came back as random number).
            #   Round-number stacks = rebuy; zero = lost without insurance.
            insurance_results = self._infer_insurance(cur, final_stacks)
            if insurance_results:
                cur.raw_data["insurance_inferred"] = insurance_results
            # hands.result 赢家归属(2026-06-11):双独立信源 — ① +xx 黄字 latch(显示层
            # 直读赢家座+赢额,盲标~100%)② chip_reconstruction(端点筹码差推赢家+净额)。
            # 两源一致 → 高置信归属;不一致两边照存(sources_agree=False),裁决权归审计/
            # 求解器,识别层不拍板。修前 133 手 result 全 null(+xx 扫描被 _empty_refs 闸死)。
            _recon_w = (cur.raw_data.get("chip_reconstruction") or {}).get("winners") or []
            _xx = sorted(self._hand_win_seats)
            cur.result = {
                "win_seats_xx": _xx,
                "win_amounts_xx": {str(s): a for s, a in sorted(self._hand_win_amounts.items())},
                "winners_endpoint": _recon_w,
                "sources_agree": (set(_xx) == set(_recon_w)) if (_xx or _recon_w) else None,
            }
            # Showdown card detection: scan non-folded seats' fold_area with CNN.
            # WePoker reveals 2 hole cards at avatar center at showdown.
            _sc = time.perf_counter()
            showdown_cards = self._capture_showdown_cards()
            if hasattr(self, "_tick_extra_ms"):
                self._tick_extra_ms["showdown_cnn"] = (time.perf_counter() - _sc) * 1000.0
            if showdown_cards:
                cur.raw_data["showdown_cards"] = showdown_cards

        hand = self.tracker.finalize_hand()
        if hand and db is not None:
            try:
                self.hand_repo.update(db, hand)
            except ValueError as e:
                # T20(2026-05-28):Ctrl+C 打在 _start_new_hand 还没 commit
                # 的瞬间,_shutdown 调 _end_current_hand → 这里 update 抛
                # ValueError(Hand X not found)。graceful skip 不让 shutdown crash。
                logger.warning(f"Hand finalize skipped (likely Ctrl+C race): {e}")
                return
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

    def _upgrade_tempuser_to_real(self, sidx: int, old: str, new: str) -> None:
        """T41(2026-05-29):TempUser 占位升级到真名 — DB sync 把旧 TempUser 行
        改写为新真名,避免画像统计把同一玩家算成 2 个身份。

        调用条件:`_capture_player_ids` 检测到 seat 之前是 TempUser_xxx
        缓存,现在 OCR 出真名 → 调本函数补 action_events 历史。
        """
        if not self._db_enabled:
            return
        try:
            with SessionLocal() as session:
                result = session.execute(
                    sql_text("UPDATE action_events SET player_name = :new "
                             "WHERE player_name = :old"),
                    {"new": new, "old": old},
                )
                session.commit()
                rowcount = result.rowcount or 0
            logger.info(f"_capture_player_ids: seat_{sidx} TempUser 升级 "
                        f"{old!r} → {new!r}({rowcount} action_events 已 sync)")
            diag.emit("player.tempuser_upgraded",
                      {"seat": sidx, "from": old, "to": new,
                       "rows_updated": rowcount},
                      hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None)
        except Exception:
            logger.warning("TempUser upgrade DB UPDATE failed", exc_info=True)

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
        # T41(2026-05-29):池子扩到 _avatar_fingerprints.values(),覆盖 zii→zi
        # 同 seat 时间错峰场景(seat X 先 cache zii,seat swap 后 cache zi;
        # 两者从未同时在 player_id_map → 旧逻辑漏 merge,DB 里两个独立 alias)。
        names_set = (set(self.tracker.player_id_map.values())
                     | set(self.tracker._avatar_fingerprints.values()))
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
                # T64a(2026-05-29):TempUser_<phash> 是空座背景 placeholder,
                # 不是"真玩家不同写法".跨 TempUser 合并会把 15+ 个空座事件
                # 黏成 1 个鬼玩家.让 T18 / T63 空座 detection 路径处理 placeholder,
                # canonicalize 只处理真名 alias.
                if n_i.startswith('TempUser_') or n_j.startswith('TempUser_'):
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
            # fold/empty 座 → 跳过
            if self.tracker.is_skippable_seat(sidx):
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
                if VERBOSE_DIAG:  # 2026-06-01:高频 spent 探针,默认关
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

    # ── R15 修正 (2026-06-09): 胜率% 不在 stack 区,在筹码【下方独立区】 ──
    # 旧前提(2026-05-31, T103)错:以为 all-in 后 stack 区改显胜率% → 给 allowlist
    # 加 "%"、读到 % 返 None 当 all-in 标记。用户 2026-06-09 实操复核:stack 区始终
    # 是筹码,胜率% 在筹码【下方独立区域】。故 "%" 留在 stack allowlist 反成 OCR 噪声源
    # (空/筹码背景被量化成 "%" → 丢读 / 疑长局假"8%"之源)。
    # 修:stack allowlist 去 "%"。真正的胜率%-区(all-in 持久信号)待另框为独立 zone。
    # 4 个 stack OCR call site 统一走这 helper。
    def _ocr_stack_chips(self, img, seat_idx: int | None = None) -> float | None:
        """OCR stack_area → 筹码数(stack 区只有筹码,无 %)。无数字 → None。"""
        text = self.ocr.read_text(img, allowlist="0123456789.")
        return self._stack_text_to_chips(text, seat_idx)

    def _reader_for(self, zone: str):
        """各区独立模板:取该区 DigitReader;缺则回退 default(stack)reader(向后兼容、
        未采区不退化)。返回 None 仅当配方整体未启用。"""
        return self._zone_readers.get(zone, self._digit_reader)

    def _stack_chips_recipe(self, crop, seat_idx: int | None = None) -> float | None:
        """杠杆A 共享读法:配方主读 stack(读空回落 EasyOCR,认 %=all-in→None)。
        flag 关 / 无 reader → 纯 EasyOCR。供热路径 + _detect_empty_seats 复用。"""
        if DIGIT_RECIPE_LIVE and self._digit_reader is not None:
            v = self._digit_reader.read(crop)
            if v is not None:
                return float(v)
        return self._ocr_stack_chips(crop, seat_idx=seat_idx)

    def _stack_text_to_chips(self, text: str, seat_idx: int | None = None) -> float | None:
        """stack OCR 文本 → 筹码值后处理(批/热路径共用)。stack 区只有筹码、无 %
        (胜率% 在筹码下方独立区,见 R15 修正 2026-06-09)。"""
        return ActionRecognizer._extract_amount(text or "")

    def _detect_allin_stackzero(self, sidx, stack_now):
        """#243【当家 2026-06-10】:stack→0 稳定 + 本手活跃(card_marker 持牌)→ 标 all-in +
        返回归0前筹码(amount)。seat 循环据此合成 "全押" 喂【现有 all-in 记录路径】(替 fold_area
        OCR 读 "Allin" 那条——长局验:stack→0 是 OCR 严格超集,召回 35 vs 13)。
        去伪主闸=活跃集(非 raw occupancy,治座位易主过渡假阳)。返 amount(float)=触发 / None=没发。"""
        st = self._az_state.setdefault(sidx, {'last_pos': None, 'zero_run': 0, 'emitted': False, 'was_active': False})
        is_active = sidx in self._active_set
        fire, amount = allin_stackzero_step(st, stack_now, is_active, ALLIN_ZERO_RUN)
        hid = self.tracker.current_hand.id if self.tracker.current_hand else None
        if fire:
            self.tracker._went_all_in_this_hand.add(sidx)   # 标记(保险/重建);belt-and-suspenders
            diag.emit("all_in.stack_zero",
                      {"seat": sidx, "amount": amount, "zero_run": st['zero_run'],
                       "was_active": st['was_active'], "active_now": is_active}, hand_id=hid)
            return amount
        elif stack_now == 0 and st['zero_run'] == 1 and not st['emitted']:
            # near-miss 诊断:读到首个 0 却没发 → 记原因(was_active/last_pos 缺啥),短跑定位
            diag.emit("all_in.stackzero_debug",
                      {"seat": sidx, "last_pos": st['last_pos'],
                       "was_active": st['was_active'], "active_now": is_active}, hand_id=hid)
        return None

    def _flush_pending_allins(self, db):
        """all-in 写库闸·手末执行(2026-06-11,#243 收尾)。

        分层语义(回答"实时建议时四闸太严?"):检测瞬间已点亮内存 mark
        (_went_all_in_this_hand + diag)= 实时档,亚秒级,实时引擎将来订阅它;
        本方法只守【写库档】= 画像/复盘吃的耐久记录,假阳会永久毒数据。
        闸:①活跃集+②本手曾持牌(was_active latch,检测内已强制)
            ③归零持续到结算(broken 看护:结算前 stack 读回正数 = 瞬态假阳,
              live 实测两例假阳【罗湖1401/东胜57】均属此类;结算后赢家栈回填不算)
        未过闸 → emit veto 诊断丢弃(证据链在 diag 可追溯,不进主表)。
        """
        for sidx, event in self._allin_pending.items():
            st = self._az_state.get(sidx) or {}
            hid = self.tracker.current_hand.id if self.tracker.current_hand else None
            if st.get('broken'):
                diag.emit("all_in.write_vetoed",
                          {"seat": sidx, "reason": "zero_broken_before_settle",
                           "amount": event.amount}, hand_id=hid)
                continue
            if db is not None:
                try:
                    self.event_repo.create(db, event)
                except Exception:
                    logger.warning(f"all-in flush seat_{sidx} persist failed", exc_info=True)
                    continue
            diag.emit("all_in.write_confirmed",
                      {"seat": sidx, "amount": event.amount}, hand_id=hid)
        self._allin_pending = {}

    # 占用判定区:stack+id(20260603 录像验:占位紧簇 24-40、空隙 16-24、空座≈0,干净 bimodal)。
    # fold_area 剔除(头像区 弃牌/timer/摊牌/表情噪声 → 占位 hamming 12-48 乱飘、偶掉 5-10 误判空)。
    # 多区取 max:任一区 > 阈即占位(占位时 stack/id 都高,空座两区都≈0)。
    _OCCUPANCY_REGIONS = ("stack_area", "id_area")

    def _classify_occupancy(self) -> dict:
        """每座 → {seat: {occupied: bool|None, ham: {region: hamming}}}。
        live 区域 _avg_hash_64 vs 空桌基线(_empty_refs):任一区 hamming > 阈 → 占位;
        全 ≤ 阈 → 空座。基线缺该座/区 → occupied=None。_empty_refs 为空 → 返回 {}。
        #241 rebuy 前置:喂"爆码座 absent→present"判定;ham 打印供 live 调阈。"""
        if not self._empty_refs:
            return {}
        out = {}
        for seat in self.roi_manager.rois.seat_regions:
            refs = self._empty_refs.get(str(seat.seat_index))
            if not refs:
                out[seat.seat_index] = {"occupied": None, "ham": {}}
                continue
            hams = {}
            for region in self._OCCUPANCY_REGIONS:
                ref = refs.get(region)
                roi = getattr(seat, region, None)
                if not ref or roi is None or getattr(roi, "width", 0) < 2:
                    continue
                img = self.capturer.capture_roi(roi)
                if img is None or img.size == 0:
                    continue
                hams[region] = _hamming(_avg_hash_64(img), ref["hash"])
            occ = (max(hams.values()) > self._occupancy_th) if hams else None
            out[seat.seat_index] = {"occupied": occ, "ham": hams}
        return out

    def _scan_win_amount(self, active_seats=None):
        """#241 +xx 赢家 latch:per-tick 扫每座 win_amount 区【黄色像素占比】> 阈 → 该座这手
        "合法进账"(赢/边池/保险,+xx 数字是黄色,色盲 avg_hash 会被任何变化误报已弃)→ 锁进
        _hand_win_seats(瞬态,某 tick 见到即锁)。喂 rebuy:爆码座下手变正【无 +xx】=rebuy /【有】=合法回血。
        active_seats 非空 → 只扫这些座(只有牌里的人能赢 + 缩面 + 滤非活跃座聊天);空 → 全座。"""
        hlo, hhi, smin, vmin = self._yellow_hsv
        for seat in self.roi_manager.rois.seat_regions:
            si = seat.seat_index
            if active_seats is not None and si not in active_seats:
                continue  # 只扫活跃集座
            already = si in self._hand_win_seats
            # 2026-06-11:已锁但金额还没读到 → 继续扫(+xx 数字比黄色出现略晚/有动画,首黄帧
            # 常读不出);拿到非 None 金额后才停(后读覆盖先读=取更 settle 的值)。
            if already and si in self._hand_win_amounts and not self._labeler.enabled:
                continue
            roi = getattr(seat, "win_amount_area", None)
            if roi is None or getattr(roi, "width", 0) < 2:
                continue
            img = self.capturer.capture_roi(roi)
            if img is None or img.size == 0:
                continue
            bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR) if img.ndim == 3 and img.shape[2] == 4 else img
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            m = ((hsv[..., 0] >= hlo) & (hsv[..., 0] <= hhi) &
                 (hsv[..., 1] >= smin) & (hsv[..., 2] >= vmin))
            if int(m.sum()) <= self._win_yellow_count:  # 黄像素数量(框无关,治稀释)
                continue
            # +xx 在显:读金额(digit 配方,allow_icon 丢首格"+";winxx 无专模板时 _reader_for 回退 stack)
            # + 信源验证抽头(LABEL_SIGNAL=win_amount 时存 crop+读值供盲标)。先用现有模板看准度,不行再自建。
            _amt = None
            if DIGIT_RECIPE_LIVE and self._digit_reader is not None:
                _amt = self._reader_for("winxx").read(img, allow_icon=True)
            if self._labeler.enabled:
                self._labeler.tap("win_amount", img, float(_amt) if _amt is not None else None,
                                  wide_frame=self.capturer.get_cached_frame(), roi=roi, seat=si,
                                  hand_id=(self.tracker.current_hand.id if self.tracker.current_hand else None))
            if _amt is not None:
                self._hand_win_amounts[si] = float(_amt)  # 赢额 latch(喂 hands.result 归属)
            if not already:
                self._hand_win_seats.add(si)  # presence latch(#241)不变

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
            # T103 R15: %-aware stack OCR(all-in 后显胜率不当 chips)
            amount = self._ocr_stack_chips(img, seat_idx=seat.seat_index)
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

    def _detect_empty_seats(self) -> None:
        """T46-A(2026-05-29):hand-start 扫描所有 seat,双确认空座.

        判定规则(两个信号同时满足):
          (a) avatar phash 严格等于 "0" * 64(纯背景 / "+" 号 = 全 0 灰度)
          (b) stack ROI OCR 提取 amount == None(没有数字 = 桌面背景)

        命中 → add to self.tracker._empty_seats → action loop 顶部统一跳过,
        本手剩余 ticks 不再读这个 seat 任何 ROI、不再生成 action_event。

        T63(2026-05-29)minimum guard 加场:同 phash 出现 ≥ 2 seat → 全标空座.
        真活玩家 avatar 唯一,空座背景几乎相同 → 重复 64-bit hash = 强空座信号.
        治 11100111 / 益力多加冰 类 (T44-A 全 0 guard 漏掉的 non-zero 重复 hash).

        重置时机:跟 _folded_seats 同步,start_new_hand 自动清零。
        """
        ZERO_HASH = "0" * 64
        seat_phashes = {}  # T63:收集 8 seat phash 做 dup detect
        for seat in self.roi_manager.rois.seat_regions:
            sidx = seat.seat_index
            avatar_zero = False
            phash = None
            if seat.fold_area is not None and seat.fold_area.width > 0:
                avatar_img = self.capturer.capture_roi(seat.fold_area)
                if avatar_img is not None and avatar_img.size > 0:
                    phash = _avg_hash_64(avatar_img)
                    seat_phashes[sidx] = phash
                    avatar_zero = (phash == ZERO_HASH)
            stack_empty = True
            if seat.stack_area is not None and seat.stack_area.width > 0:
                stack_img = self.capturer.capture_roi(seat.stack_area)
                # T103 R15: %-aware(all-in equity returns None,触发 stack_empty)
                # 杠杆A:这 8 次 stack 读也走配方(快赢,削 startup 卡)。
                stack_empty = (self._stack_chips_recipe(stack_img, seat_idx=sidx) is None)
            if avatar_zero and stack_empty:
                self.tracker._empty_seats.add(sidx)
                diag.emit(
                    "seat.empty_detected",
                    {"seat": sidx, "reason": "avatar_zero_hash + stack_no_digit"},
                    hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None,
                )
                logger.info(f"_detect_empty_seats: seat_{sidx} 标空座(本手跳过)")

        # T63 minimum guard:同 phash >= 2 seat → 全标空座.
        from collections import Counter
        hash_counts = Counter(seat_phashes.values())
        for sidx, ph in seat_phashes.items():
            if hash_counts[ph] >= 2 and sidx not in self.tracker._empty_seats:
                self.tracker._empty_seats.add(sidx)
                diag.emit(
                    "seat.empty_detected",
                    {"seat": sidx,
                     "reason": "duplicate_phash_minimum_guard",
                     "phash_prefix": ph[:8],
                     "occurrences": hash_counts[ph]},
                    hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None,
                )
                logger.info(
                    f"_detect_empty_seats(T63): seat_{sidx} 标空座 "
                    f"(dup phash {ph[:8]} × {hash_counts[ph]})"
                )

    # T52(2026-05-29):pixel diff trigger 阈值.
    # Phase 0 实测 cv2.absdiff 单 ROI < 1μs.阈值 diff_per_pixel < 3 起步保守,
    # 即"几乎完全没变"才 reuse cache.太严 → 命中率低,T52 几乎无收益;
    # 太松 → real overlay 漂移被认为无变化,漏抓.
    # Tuning thread:实测后调.
    _DIFF_THRESHOLD = 3.0

    def _get_tick_frame_grey(self):
        """#237:整帧灰度图,每 tick 算一次缓存(recognize-only 用,避免逐座 cvtColor 整帧)。
        无 D.1 缓存帧(FRAME_CAPTURE 关 / 尚未 refresh)→ None → 调用方回退 read_text。"""
        tick = self.tracker._global_tick_counter
        if getattr(self, "_frame_grey_tick", None) == tick:
            return self._frame_grey
        frame = self.capturer.get_cached_frame()
        if frame is None or frame.size == 0:
            grey = None
        elif frame.ndim == 3 and frame.shape[2] == 4:
            grey = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
        elif frame.ndim == 3:
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            grey = frame  # 已是单通道
        self._frame_grey = grey
        self._frame_grey_tick = tick
        return grey

    def _recognize_only_roi(self, roi, allowlist):
        """#237:recognize-only 读单个 ROI。用每 tick 缓存的整帧灰度图 + roi 帧内框,
        跳过 CRAFT 检测。返回 None = 无法走此路(无缓存帧 / 坐标越界)→ 调用方回退 read_text。"""
        grey = self._get_tick_frame_grey()
        if grey is None:
            return None
        h, w = grey.shape[:2]
        x0, y0 = roi.left, roi.top
        x1, y1 = roi.left + roi.width, roi.top + roi.height
        if x0 < 0 or y0 < 0 or x1 > w or y1 > h or x1 <= x0 or y1 <= y0:
            return None
        out = self.ocr.recognize_boxes(grey, [[x0, x1, y0, y1]], allowlist=allowlist)
        return out[0] if out else ""

    def _capture_with_diff_trigger(self, roi_key: str, roi,
                                    allowlist: str = "",
                                    ensemble: bool = False,
                                    force_every_n_ticks: int = 4) -> tuple:
        """T52(2026-05-29):pixel diff trigger 的 OCR 包装器.

        - 抓 ROI 图像 → 跟上次抓帧 cv2.absdiff
        - diff < 阈值 → reuse 上次 OCR 结果(省一次 OCR ~10ms)
        - diff >= 阈值 OR force_refresh tick → 真做 OCR + cache 更新

        Force-refresh 守护:每 N tick(默认 4 = ~1s)force re-OCR,防 stale cache.

        Returns:
            (ocr_text, captured_img)
        """
        img = self.capturer.capture_roi(roi)
        if img is None or img.size == 0:
            return "", img

        cached_img = self.tracker._last_roi_img.get(roi_key)
        cached_text = self.tracker._last_roi_text.get(roi_key)
        tick_now = self.tracker._global_tick_counter
        last_force_tick = self.tracker._roi_force_refresh_at.get(roi_key, 0)
        need_force_refresh = (tick_now - last_force_tick) >= force_every_n_ticks

        if (cached_img is not None and cached_text is not None
                and not need_force_refresh
                and cached_img.shape == img.shape):
            diff = cv2.absdiff(img, cached_img).sum()
            diff_per_pixel = float(diff) / (img.size or 1)
            if diff_per_pixel < self._DIFF_THRESHOLD:
                # 内容几乎没变 → 复用 OCR 结果
                diag.emit(
                    "ocr.diff_skip",
                    {"roi_key": roi_key,
                     "diff_per_pixel": round(diff_per_pixel, 2)},
                    hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None,
                    level="DEBUG",
                )
                return cached_text, img

        # 真做 OCR.#237:flag 开 → recognize-only(跳检测,与上面 diff 缓存叠加);
        # 无缓存帧/坐标越界 → _recognize_only_roi 返 None → 回退 read_text(零风险)。
        text = None
        if OCR_RECOGNIZE_ONLY:
            text = self._recognize_only_roi(roi, allowlist)
        if text is None:
            text = self.ocr.read_text(img, allowlist=allowlist, ensemble=ensemble)
        self.tracker._last_roi_img[roi_key] = img
        self.tracker._last_roi_text[roi_key] = text
        self.tracker._roi_force_refresh_at[roi_key] = tick_now
        return text, img

    def _pre_batch_action_amount_ocr(self, rois):
        """T73(2026-05-29):pre-batch action + amount OCR before sequential seat loop.

        预先一次 GPU call batch 所有 active seat 的 action_area + amount_area OCR.
        即使 timer-handled seat 也 batch(浪费的算力,batch 模式低成本).
        结果存 self._batched_action_results / self._batched_amount_results dict.
        Sequential 路径(OCR_BATCH=0)时此方法 noop.
        """
        self._batched_action_results = {}
        self._batched_amount_results = {}
        self._batched_amount_crops = {}   # cut1:配方读 amount 用 crop
        from config import OCR_BATCH
        if not OCR_BATCH:
            return

        # Phase 1: 收集 action images
        action_items = []
        amount_items = []
        for seat_roi in rois.seat_regions:
            sidx = seat_roi.seat_index
            # fold/empty 座 → 跳过
            if self.tracker.is_skippable_seat(sidx):
                continue
            if seat_roi.action_area is not None and seat_roi.action_area.width > 0:
                img = self.capturer.capture_roi(seat_roi.action_area)
                if img is not None and img.size > 0:
                    action_items.append((sidx, img))
            if seat_roi.amount_area is not None and seat_roi.amount_area.width > 0:
                img = self.capturer.capture_roi(seat_roi.amount_area)
                if img is not None and img.size > 0:
                    amount_items.append((sidx, img))

        # Phase 2: batch action OCR
        if action_items:
            images = [img for (_, img) in action_items]
            results = self.ocr.read_text_batch(
                images, allowlist=ACTION_OCR_ALLOWLIST, scale=3
            )
            for (sidx, _), text in zip(action_items, results):
                self._batched_action_results[sidx] = text

        # Phase 3: batch amount OCR(cut1:配方开→留 crop、跳 EasyOCR 批读;消费端配方读+兜底)
        for sidx, img in amount_items:
            self._batched_amount_crops[sidx] = img
        if amount_items and not (DIGIT_RECIPE_LIVE and self._digit_reader is not None):
            images = [img for (_, img) in amount_items]
            results = self.ocr.read_text_batch(
                images, allowlist="0123456789.", scale=3
            )
            for (sidx, _), text in zip(amount_items, results):
                self._batched_amount_results[sidx] = text

    def _pre_batch_seat_state_ocr(self, rois):
        """2026-06-01 spike A:把逐座 timer / fold_text / fold_area OCR 批成 3 次
        GPU call(原 ~24 次独立调用 = seat_actions 真瓶颈,GPU 异步下被低估)。
        各 allowlist 不同 → 分 3 组各一次 batch。fold_area 同时存 img(avatar
        hash / 亮度仍需原图)。BATCH_SEAT_OCR=0 时 noop → 逐座旧路径。
        """
        self._batched_timer_results = {}
        self._batched_fold_results = {}  # sidx -> (img, text)
        self._batched_stack_results = {}
        self._batched_stack_crops = {}   # sidx -> img(杠杆A:配方读 stack 用 crop)
        if not BATCH_SEAT_OCR:
            return
        # #235(2026-06-08):删【专读弃牌 fold_text_area】OCR。弃牌识别改:非river=活跃集(card_marker
        # 消失,已在跑)/ river=fold_area 读"弃牌"(摊牌免疫,本就保留管 all-in/基线)。fold_text_area
        # 与 fold_area 重复 → 删它省一次 batch OCR。fold_area 全留(all-in/baseline/river弃牌)。
        timer_items, fold_items, stack_items = [], [], []
        for seat_roi in rois.seat_regions:
            sidx = seat_roi.seat_index
            if self.tracker.is_skippable_seat(sidx):
                continue
            if seat_roi.timer_area is not None and seat_roi.timer_area.width > 0:
                img = self.capturer.capture_roi(seat_roi.timer_area)
                if img is not None and img.size > 0:
                    timer_items.append((sidx, img))
            if seat_roi.fold_area is not None and seat_roi.fold_area.width > 0:
                img = self.capturer.capture_roi(seat_roi.fold_area)
                if img is not None and img.size > 0:
                    fold_items.append((sidx, img))
            if seat_roi.stack_area is not None and seat_roi.stack_area.width > 0:
                img = self.capturer.capture_roi(seat_roi.stack_area)
                if img is not None and img.size > 0:
                    stack_items.append((sidx, img))
        if timer_items:
            texts = self.ocr.read_text_batch([i for _, i in timer_items], allowlist="0123456789s ")
            for (sidx, _), t in zip(timer_items, texts):
                self._batched_timer_results[sidx] = t
        # #235 part2(2026-06-10):剥 fold_area batch OCR(~199ms,seat_actions 真瓶颈)。
        # all-in→stack→0 / 弃牌→活跃集+闸 / timer→timer_area,fold_area 不再需 read_text。
        # 仍存 img 给 avatar baseline;text 恒 None(下游已不读)。
        for sidx, img in fold_items:
            self._batched_fold_results[sidx] = (img, None)
        # 杠杆A:配方开 → 留 crop 给 DigitReader、跳过 EasyOCR stack 批读(省那次 OCR);
        #   配方读空(全下%/不可读)时,消费端再对该 crop 走 EasyOCR 兜底(认 %)。
        for sidx, img in stack_items:
            self._batched_stack_crops[sidx] = img
        if stack_items and not (DIGIT_RECIPE_LIVE and self._digit_reader is not None):
            texts = self.ocr.read_text_batch([i for _, i in stack_items], allowlist="0123456789.")  # stack 区无 %(R15 修正)
            for (sidx, _), t in zip(stack_items, texts):
                self._batched_stack_results[sidx] = t

    def _process_seat_actions(self, db, rois):
        # NB: iterate using seat_roi.seat_index (NOT enumerate's i) for all
        # tracker state lookups — list-position differs from physical seat_index
        # when only some seats are configured (e.g. partial stage-B setup).
        # T57(2026-05-29):seat 内子 phase 累计,跨 8 seat sum.
        # T73(2026-05-29):OCR_BATCH 时先预 batch action + amount,seat 循环命中 dict.
        sub_ms = {
            "seat_stack_ocr": 0.0,
            "seat_timer_ocr": 0.0,
            "seat_fold_ocr": 0.0,
            "seat_action_ocr": 0.0,
            "seat_amount_ocr": 0.0,
            "seat_avatar_hash": 0.0,
            "seat_parse_persist": 0.0,
            # 2026-06-01 spike A:未计时 gap 定位
            "seat_artifact": 0.0,
        }
        # T73:pre-batch(OCR_BATCH=0 时 noop)
        _t = time.perf_counter()
        self._pre_batch_action_amount_ocr(rois)
        sub_ms["seat_action_ocr"] += (time.perf_counter() - _t) * 1000.0
        # 2026-06-01 spike A:pre-batch 逐座 timer/fold_text/fold_area(BATCH_SEAT_OCR=0 noop)
        _t = time.perf_counter()
        self._pre_batch_seat_state_ocr(rois)
        sub_ms["seat_fold_ocr"] += (time.perf_counter() - _t) * 1000.0

        _loop_t0 = time.perf_counter()  # 2026-06-05:整 seat 循环计时(确认 gap 在循环内)
        for seat_roi in rois.seat_regions:
            sidx = seat_roi.seat_index

            # T46-A(2026-05-29):inactive seat 统一 guard — 已 fold 或空座 → 本手
            # 剩余 ticks 全跳过。fold 是用户 2026-05-29 观察"灰头像+弃牌字稳定显示"
            # 提的 insight,empty 是空座位"+号+背景色"同样稳定。一个 guard 治死人
            # 复活 + 空座 TempUser 噪音两个 P1 bug。
            # fold/empty 座 → 跳过
            if self.tracker.is_skippable_seat(sidx):
                continue

            # P1 cross-validation: always read stack every tick (not just on action change)
            # so we have stack_before/stack_after on the action that DOES change.
            stack_now = None
            if seat_roi.stack_area is not None and seat_roi.stack_area.width > 0:
                _t = time.perf_counter()
                # T103 R15: %-aware stack OCR(all-in equity returns None)
                if DIGIT_RECIPE_LIVE and self._digit_reader is not None:
                    # 杠杆A:配方主读(快~10×)。crop 优先用 batch 已抓的,无则现抓。
                    crop = getattr(self, "_batched_stack_crops", {}).get(sidx)
                    if crop is None:
                        crop = self.capturer.capture_roi(seat_roi.stack_area)
                    v = self._digit_reader.read(crop)
                    if v is not None:
                        stack_now = float(v)
                    else:
                        # 配方读空(全下%/不可读)→ EasyOCR 兜底(认 %=all-in→None+emit)
                        stack_now = self._ocr_stack_chips(crop, seat_idx=sidx)
                elif BATCH_SEAT_OCR and sidx in self._batched_stack_results:
                    stack_now = self._stack_text_to_chips(self._batched_stack_results[sidx], sidx)  # spike A: batched
                else:
                    stack_img = self.capturer.capture_roi(seat_roi.stack_area)
                    stack_now = self._ocr_stack_chips(stack_img, seat_idx=sidx)
                sub_ms["seat_stack_ocr"] += (time.perf_counter() - _t) * 1000.0
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

            # #243 当家:stack→0 稳定 + 活跃集 → 标 all-in + 返归0前筹码;下方合成 "全押" 喂记录路径
            az_allin_amount = None
            if ALLIN_STACKZERO and self.tracker.has_active_hand:
                az_allin_amount = self._detect_allin_stackzero(sidx, stack_now)
                # 四闸③"归零持续到结算"看护(2026-06-11):fire 后若【结算开始前】stack 又读回
                # 正常数字 = 瞬态假阳(座位易主/误读,live 实测罗湖1401/东胜57 两例)→ 标 broken,
                # 手末 flush 时拒写。结算后(总底池latch/摊牌latch)赢家栈合法回填,不算 broken —
                # 否则会误杀"全推后赢了翻倍"的真 all-in。容差 >5 同 12a 的 stack_after 判零口径。
                _az_st = self._az_state.get(sidx)
                if (_az_st and _az_st.get('emitted') and not _az_st.get('broken')
                        and stack_now is not None and stack_now > 5
                        and not (self._pot_label_latched or self._showdown_latched)):
                    _az_st['broken'] = True
                    diag.emit("all_in.zero_broken",
                              {"seat": sidx, "stack_now": stack_now},
                              hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None)

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
                _t = time.perf_counter()
                if BATCH_SEAT_OCR and sidx in self._batched_timer_results:
                    timer_text = self._batched_timer_results[sidx]  # spike A: batched
                else:
                    timer_img = self.capturer.capture_roi(seat_roi.timer_area)
                    timer_text = self.ocr.read_text(timer_img, allowlist="0123456789s ")
                sub_ms["seat_timer_ocr"] += (time.perf_counter() - _t) * 1000.0
                tm = re.search(r"\b(\d{1,2})\b", timer_text or "") if timer_text else None
                if tm and 0 <= int(tm.group(1)) <= 60:
                    self._process_timer(sidx, int(tm.group(1)))
                    if stack_now is not None:
                        self.tracker._prev_stack[sidx] = stack_now
                    timer_handled = True
            if timer_handled:
                # 吞街修补漏(2026-06-11 复盘):timer 在此 continue → 空白游程不会累积 →
                # "过牌→思考计时→又过牌"仍被 prev 文本吞。timer 出现 = 该座正在决策 =
                # 上一动作气泡必然已结束 → 直接清 prev(语义比"等空白"强,且覆盖"气泡
                # 无空白期直接切 timer"的盲区)。
                self.tracker._prev_action_texts.pop(sidx, None)
                self._action_blank_run[sidx] = 0
                continue

            # #235(2026-06-08):删 T120 专读弃牌 fold_text_area 块(与 fold_area 重复)。
            # 弃牌识别:非river=活跃集(_rescue_silent_fold,card_marker消失)/ river=下方 fold_area
            # 读"弃牌"(摊牌免疫:弃牌显"弃牌"字、摊牌显正面牌无此字)。fold_area 全留(all-in/timer/
            # baseline/river弃牌)。所知风险:T120 修过的座(0/4/5/6/7)river 弃牌回落 fold_area 较糙读
            # → 非river 有活跃集兜,river 那几座有残余漏读风险,下次录制验。
            if seat_roi.fold_area is not None:
                # #235 part2(2026-06-10):剥 fold_area OCR(原 seat_fold_ocr ~199ms,62% tick)。
                # fold_area 文字三用途全已迁出:弃牌→活跃集+摊牌闸 / timer→专用 timer_area /
                # all-in→stack→0(#243 当家)。仅留 capture 给 idle_avatar baseline;read_text 删。
                if BATCH_SEAT_OCR and sidx in self._batched_fold_results:
                    fold_img, _ = self._batched_fold_results[sidx]  # img 仍存(avatar hash);text 恒 None
                else:
                    fold_img = self.capturer.capture_roi(seat_roi.fold_area)
                # all-in:stack→0(#243)触发 → 合成 "全押" 喂现有 all-in 记录路径(替 fold_area "Allin")
                if action_text is None and az_allin_amount is not None:
                    action_text = "全押"
                    self._finalize_timer(sidx)  # timer ended via all-in
                else:
                    # idle / between actions:收尾 timer + 更新 idle_avatar baseline(摊牌幻觉检测用)
                    self._finalize_timer(sidx)
                    # 仅非 skippable(fold/empty)座更新 idle baseline
                    if not self.tracker.is_skippable_seat(sidx) and fold_img is not None and fold_img.size > 0:
                        _t = time.perf_counter()
                        self.tracker._idle_avatar_hash[sidx] = _avg_hash_64(fold_img)
                        sub_ms["seat_avatar_hash"] += (time.perf_counter() - _t) * 1000.0

            # #240:本座是否走"动作读取"(phash/OCR)——fold/all-in 已在上面经 fold_area 设了
            # action_text,_need_action_read=False,下面金额拼接跳过它(避免给 fold 读金额)。
            _need_action_read = action_text is None
            if action_text is None and self._action_phash is not None:
                # #240(2026-06-07):text-shape phash 二元桩替 action OCR。匹配→中文动作词(喂下方
                # ActionRecognizer.parse);落空→""(无动作/idle,不回退 OCR——phash 即权威)。
                # T52 同款 diff 缓存:action_area 未变则复用上次结果(动作持久态→多数 tick 不重算)。
                _t = time.perf_counter()
                action_img = self.capturer.capture_roi(seat_roi.action_area)
                _rk = f"seat_{sidx}_action"
                _cached = self.tracker._last_roi_img.get(_rk)
                _tick = self.tracker._global_tick_counter
                _need_force = (_tick - self.tracker._roi_force_refresh_at.get(_rk, 0)) >= 4
                if (_cached is not None and not _need_force and action_img is not None
                        and _cached.shape == action_img.shape
                        and float(cv2.absdiff(action_img, _cached).sum()) / (action_img.size or 1) < self._DIFF_THRESHOLD):
                    action_text = self.tracker._last_roi_text.get(_rk, "")  # 未变 → 复用
                else:
                    word = self._action_phash.match(action_img)
                    action_text = word if word else ""
                    self.tracker._last_roi_img[_rk] = action_img
                    self.tracker._last_roi_text[_rk] = action_text
                    self.tracker._roi_force_refresh_at[_rk] = _tick
                sub_ms["seat_action_ocr"] += (time.perf_counter() - _t) * 1000.0

            if action_text is None:
                # T73(2026-05-29):OCR_BATCH 时优先用 pre-batch 结果(跳过 _capture_with_diff_trigger).
                from config import OCR_BATCH
                if OCR_BATCH and sidx in self._batched_action_results:
                    action_text = self._batched_action_results[sidx]
                    # 抓 img 给 T1 review artifact + 后续 cache 更新
                    # 2026-06-01 spike A:计时这个未计时的 artifact 截图
                    _t = time.perf_counter()
                    action_img = self.capturer.capture_roi(seat_roi.action_area)
                    # 维护 T52 cache 一致性(下次若 OCR_BATCH=0 仍可命中)
                    self.tracker._last_roi_img[f"seat_{sidx}_action"] = action_img
                    self.tracker._last_roi_text[f"seat_{sidx}_action"] = action_text
                    sub_ms["seat_artifact"] += (time.perf_counter() - _t) * 1000.0
                else:
                    # T52(2026-05-29):pixel diff trigger 包装 — Phase 0 实测 9.17x
                    # speedup,大部分 tick action overlay 无变化 → reuse cache 省 OCR.
                    # #8 ensemble 同时启用(diff 命中时 cache 命中 ensemble 结果也省).
                    _t = time.perf_counter()
                    action_text, action_img = self._capture_with_diff_trigger(
                        roi_key=f"seat_{sidx}_action",
                        roi=seat_roi.action_area,
                        allowlist=ACTION_OCR_ALLOWLIST,
                        ensemble=True,
                    )
                    sub_ms["seat_action_ocr"] += (time.perf_counter() - _t) * 1000.0

            # 金额拼接:phash 与 OCR 两路共用(#240 2026-06-07 修——原在 OCR 块内,phash 设了
            # action_text 就把金额拼接整段跳过 → phash 认出的 call/raise/bet 丢金额)。
            # _need_action_read 排除 fold-early(给 fold 读金额无意义)。
            if _need_action_read and action_text and seat_roi.amount_area is not None:
                from config import OCR_BATCH
                # cut1:配方主读 amount(allow_icon 丢筹码图标留数字),读空回落 EasyOCR。
                if DIGIT_RECIPE_LIVE and self._digit_reader is not None:
                    _t = time.perf_counter()
                    crop = getattr(self, "_batched_amount_crops", {}).get(sidx)
                    if crop is None:
                        crop = self.capturer.capture_roi(seat_roi.amount_area)
                    # 各区模板:amount 专用 reader(无 _amount.json 则回退 stack/default)
                    v = self._reader_for("amount").read(crop, allow_icon=True)
                    if self._labeler.enabled:  # 信源验证抽头(LABEL_SIGNAL=amount)
                        self._labeler.tap("amount", crop, float(v) if v is not None else None,
                                          wide_frame=self.capturer.get_cached_frame(),
                                          roi=seat_roi.amount_area, seat=sidx,
                                          hand_id=(self.tracker.current_hand.id if self.tracker.current_hand else None))
                    if v is not None:
                        amount_text = str(int(v))
                    else:
                        amount_text, _ = self._capture_with_diff_trigger(
                            roi_key=f"seat_{sidx}_amount", roi=seat_roi.amount_area,
                            allowlist="0123456789.", ensemble=False)
                    self.tracker._last_roi_text[f"seat_{sidx}_amount"] = amount_text
                    sub_ms["seat_amount_ocr"] += (time.perf_counter() - _t) * 1000.0
                # T73:OCR_BATCH 优先用 pre-batch.
                elif OCR_BATCH and sidx in self._batched_amount_results:
                    amount_text = self._batched_amount_results[sidx]
                    self.tracker._last_roi_text[f"seat_{sidx}_amount"] = amount_text
                else:
                    _t = time.perf_counter()
                    amount_text, _ = self._capture_with_diff_trigger(
                        roi_key=f"seat_{sidx}_amount",
                        roi=seat_roi.amount_area,
                        allowlist="0123456789.",
                        ensemble=False,
                    )
                    sub_ms["seat_amount_ocr"] += (time.perf_counter() - _t) * 1000.0
                if amount_text:
                    action_text = f"{action_text} {amount_text}"

            if not action_text:
                # No event this tick — still update _prev_stack so the NEXT event has
                # an accurate stack_before reading from the same baseline.
                if stack_now is not None:
                    self.tracker._prev_stack[sidx] = stack_now
                # 吞街修(2026-06-11 验尸):check_action_change 的 prev 文本只在非空时更新、
                # 空白期从不清 → "flop过牌→空白→turn又过牌"被判没变化,整街隐身(133手审计
                # 6 例,全是 turn 过牌街)。空白【连续 ≥2 帧】才清(单帧闪烁不清,防 OCR/phash
                # 抖动出"清→同 overlay 重读→重录";真清后的跨街重录由 dedup 按(player,street,
                # action)键放行——街不同,合法)。已知残余:气泡若跨街完全不消失则无空白可依,
                # 图像层不可分,留 P2 金额轨迹桩。
                _run = self._action_blank_run.get(sidx, 0) + 1
                self._action_blank_run[sidx] = _run
                if _run == 2:
                    self.tracker._prev_action_texts.pop(sidx, None)
                continue
            self._action_blank_run[sidx] = 0

            # A(2026-06-11):等金额 settle 期间(_action_amt_wait>0)即使 action_text 没变也重入 —
            # 否则金额永不 append("加注"恒定)时 check_action_change 恒 False、块被跳过、wait-cap 永不
            # 触发 → 动作丢失。有 pending wait 就强制重入,直到金额 settle 或 wait-cap 记录(防回归)。
            if self.tracker.check_action_change(sidx, action_text) or self._action_amt_wait.get(sidx, 0) > 0:
                parsed = self.action_recognizer.parse(action_text)
                # Diagnostic: log every state-changed action OCR result, even when
                # parser fails. Critical for raise-detection diagnosis.
                parsed_label = parsed["action_type"].value if parsed else "UNPARSED"
                # 2026-06-10:标签改 [OCR]→[act](动作走 phash #240,不是 OCR;旧名误导)。
                logger.info(f"[act seat_{sidx}] text={action_text!r} -> {parsed_label}")
                if parsed is None:
                    continue
                # #235 摊牌闸:本手已进结算后,fold_ocr 读到的"盖牌/弃牌"是摊牌 muck(决策点之后)→
                # 截断不收(治普通局那种 conf1.0"盖牌"假弃牌;all-in 者/赢家不会被记 fold)。
                # 幽灵fold修(2026-06-11):压制条件加"总底池 latch"——摊牌白角闸覆盖有洞
                # (06-10 审计 4 手全没 latch),而总底池 latch 132/133 手可靠且语义=进结算。
                if (((SHOWDOWN_GATE and self._showdown_latched) or self._pot_label_latched)
                        and parsed["action_type"] == ActionType.FOLD):
                    diag.emit("showdown.fold_suppressed", {"seat": sidx, "text": action_text},
                              hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None)
                    continue

                position_str = self.tracker.get_position(sidx)
                try:
                    position = Position(position_str)
                except ValueError:
                    position = Position.UTG  # fallback

                # Detect facing action from previous actions in this hand
                facing = self._build_facing_action(sidx)

                # ID 手内冻结:本手首用名锁定,中途改名(头像误evict→别名)只影响下一手
                player_name = self._hand_player_name(sidx, default=f"Player_{sidx}")
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
                # T16(2026-05-28):text 几乎永远优先。
                # baseline 验证:T9 fix 后仍 86.2% 准确率(9 个错全是 stack
                # override text 类,主要 fold→call 6 次)。根因:T9 假设
                # "text 错 stack 对",实际 chip contribution 场景反之 —
                # text 是 WePoker UI 文字明示,stack OCR 漏读概率更高。
                #
                # 新策略:**text 优先**,只在两种 sanity 失败时 stack override:
                #   (a) text=FOLD/CHECK 但 stack_delta > 2 → text 必错(玩家
                #       投了钱不可能 fold,可能 "跟注 XX" 被看成 "弃牌")
                #   (b) text != ALL_IN 但 stack_after ≈ 0 → text 漏 all-in 关键词
                text_is_zero = text_derived in (ActionType.FOLD, ActionType.CHECK)
                stack_is_chip = (stack_delta is not None and abs(stack_delta) > 2)
                stack_says_allin = (stack_derived == ActionType.ALL_IN)

                should_override = False
                if stack_derived is not None and stack_derived != text_derived:
                    if text_is_zero and stack_is_chip:
                        # 物理矛盾 (a):text=fold/check 但 stack 投了钱
                        should_override = True
                    elif text_derived != ActionType.ALL_IN and stack_says_allin:
                        # 物理矛盾 (b):text 漏 all_in 关键词
                        should_override = True

                if should_override:
                    final_action = stack_derived
                    override_reason = f"stack-derived {stack_derived.value} overrode text-derived {text_derived.value}"
                    logger.info(f"[P3 override T16] seat_{sidx} text={action_text!r} "
                                f"text→{text_derived.value} stack→{stack_derived.value}")
                elif VERBOSE_DIAG and stack_derived is not None and stack_derived != text_derived:
                    # text 优先(包括 T9 前会被 override 的 6 个 fold→call case)
                    # 2026-06-01:spent 探针,默认关(VERBOSE_DIAG)
                    diag.emit(
                        "p3.text_priority_preserved",
                        {
                            "seat": sidx,
                            "text_action": text_derived.value,
                            "stack_action": stack_derived.value,
                            "stack_delta": stack_delta,
                            "stack_after": stack_after,
                            "current_to_call": self.tracker._street_to_call,
                        },
                        hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None,
                    )

                event.action_type = final_action  # may be overridden

                # ── 金额 settle 三重闸(A / A′ / A″)+ 兜底殿后 ──────────────────
                # A (2026-06-11):overlay 首帧金额还在动画 → recipe 返 None → 推迟 commit,
                #   下帧 settle 后 action_text 变 → fresh 记录;最多等 _AMT_SETTLE_MAX 帧。
                # A′(2026-06-11):动作前 overlay 已挂旧数字(盲注/本街先前投入)→ 首帧读旧值非
                #   None 直接提交错值(修前 SB call=小盲 10/11)。规则:call 读数必 ≥ 本街最高注
                #   _street_amt_max(盲注播种 bb),bet/raise 必 >;不满足视同未 settle。
                # A″(2026-06-11 审计 1202 手):call 读数=自己刚提交的加注额(258,真值 516)
                #   能穿过 A′(等于地板不小于) → 自身规则:bet/call/raise 的新读数必须严格 >
                #   自己本街已提交额 _seat_street_amt(call/raise 必然增加自己投入,等于=陈旧)。
                #   此规则不依赖地板,对"地板被漏抓污染"免疫。
                # 闸序倒置(2026-06-11 验尸,替换 2026-06-06 的 step1 抢跑式兜底):stack 兜底
                #   原在闸前 — 首帧陈旧"加注 2"+ 同帧 stack 误读(323→真289误93)= 兜底改写 230
                #   越闸落库(真值 36),毒地板连累后续真值变 suspect、真值帧死于 dedup。倒置后:
                #   先等 settle;【只有封顶后金额仍 None/仍陈旧】才轮到 stack 兜底(其原始职责=
                #   金额彻底缺时的最后一招;干净读数永远优先于 stack 推算)。
                # 封顶提交不丢事件;封顶后仍陈旧 emit stale_suspect 留痕(含 own_prev 供归因)。
                _needs_amt = final_action in (ActionType.BET, ActionType.RAISE, ActionType.CALL)
                _amt_floor = self.tracker._street_amt_max
                _own_prev = self.tracker._seat_street_amt.get(sidx, 0.0)
                _amt_stale = (_needs_amt and event.amount is not None and (
                    (_amt_floor > 0
                     and (event.amount < _amt_floor if final_action == ActionType.CALL
                          else event.amount <= _amt_floor))
                    or event.amount <= _own_prev))
                if (_needs_amt and (event.amount is None or _amt_stale)
                        and self._action_amt_wait.get(sidx, 0) < self._AMT_SETTLE_MAX):
                    self._action_amt_wait[sidx] = self._action_amt_wait.get(sidx, 0) + 1
                    continue
                amount_override_reason = None
                if _needs_amt and (event.amount is None or _amt_stale):
                    # 封顶仍没有可信读数 → stack 兜底殿后(2026-06-06 step1 的原始场景:
                    # last-actor 短窗读成自己盲注;stack 跌幅明显大于读数才覆盖,纯逻辑已单测)
                    new_amount, amount_override_reason = reconcile_underread_amount(
                        final_action.value, event.amount, stack_delta)
                    if amount_override_reason:
                        logger.info(f"[amount兜底@cap] seat_{sidx} {amount_override_reason}")
                        event.amount = new_amount
                if _amt_stale:
                    diag.emit("amount.stale_suspect",
                              {"seat": sidx, "action": final_action.value, "amount": event.amount,
                               "street_amt_max": _amt_floor, "own_prev": _own_prev,
                               "waited": self._action_amt_wait.get(sidx, 0),
                               "reconciled": bool(amount_override_reason)},
                              hand_id=self.tracker.current_hand.id)
                self._action_amt_wait[sidx] = 0

                event.raw_data = {
                    "seat_index": sidx,   # 座位主键(2026-06-13):对账层按座取端点,免玩家名反推(治 MAPPING_GAP)
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
                    "amount_override_reason": amount_override_reason,
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
                # 2026-06-08 #226 item2:compute_confidence 只看物理(stack/pot delta),不理 text。
                # 但 phash 动作识别已验收可靠(#240 四动作全绿/零误词)→ text 确认的动作即使
                # 筹码变化没抓到(stack_delta=0,捕获漏)也不该被低档门当噪声丢(soak 实测两手
                # 翻前开池加注+多个跟注因此被丢)。phash 开时:text_derived==final_action → 置信
                # 下限 0.8 过门;dedup(5s窗)防重复读。phash 关(OCR)时不升(文字不可信,保旧行为)。
                if ACTION_PHASH_LIVE and text_derived is not None and text_derived == final_action:
                    event.confidence_score = max(event.confidence_score, 0.8)

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

                # T46-B(2026-05-29):action debounce — call/raise overlay 持续 1-2 秒
                # 4-8 个 tick 反复触发同一 action 入库。5 秒窗口内同 (player,street,
                # action_type) 视为重复,直接 skip + emit diag。
                # ⚠️2026-06-12 解冻修(bafca031 实案,识别冻结 case #1):原键只看
                # (player,street,action) 不看【金额】→ 误杀同街合法二次动作 ——
                # limp跟4→面对加注再跟36、加注36→被3bet再加注120,这些金额必变却被
                # 当重复吞(没如果有结 preflop 两次 call 间隔 4.78s<5s 被误删,大锅多bet
                # 重灾)。修:金额维度入键 —— 金额差 ≤ AMT_DUP_TOL = 同一动作重复读(去);
                # 显著不同 = 合法二次动作(留)。check/fold 金额恒 None → 退回纯时间窗
                # (同街二次 check/fold 不存在,时间窗对它们正确)。
                import time as _t
                _now_ts = _t.time()
                _action_str = (final_action.value if final_action else
                               (event.action_type.value if event.action_type else "unknown"))
                _street_str = event.street.value if event.street else ""
                _dedup_key = (event.player_name, _street_str, _action_str)
                _amt = event.amount
                _last = self.tracker._last_action_at.get(_dedup_key)   # (ts, amount) | None
                _AMT_DUP_TOL = 2.0   # 金额抖动容忍(settle 后重复读应同值;真二次动作差≫此)
                _is_dup = False
                if _last is not None and _now_ts - _last[0] < 5.0:
                    _last_amt = _last[1]
                    if _amt is None and _last_amt is None:
                        _is_dup = True                              # check/fold 重复读
                    elif (_amt is not None and _last_amt is not None
                          and abs(_amt - _last_amt) <= _AMT_DUP_TOL):
                        _is_dup = True                              # 同金额 = 同动作重复帧
                    # 否则金额显著不同 = 合法二次动作(limp→call/raise→reraise)→ 不去重
                if _is_dup:
                    diag.emit(
                        "action.dedup_skip",
                        {"player": event.player_name, "street": _street_str,
                         "action": _action_str, "amount": _amt,
                         "last_amount": _last[1] if _last else None,
                         "ts_delta_sec": round(_now_ts - _last[0], 2)},
                        hand_id=self.tracker.current_hand.id,
                    )
                    continue
                self.tracker._last_action_at[_dedup_key] = (_now_ts, _amt)

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

                # T47-V(2026-05-29):trust ladder — 低桩(conf < 0.7,纯 P3 stack-
                # derived,无 text/amount 高桩信号)不进 action_events 主表,只 emit
                # action.low_tier_skip diag 作 raw_data 证据保留可追溯。
                # 实践真知:数据看到 0.5 conf 假入库造成"同 player 同 street raise→
                # raise→call"逻辑非法 dup(33b198ae / 167371fe / a02db5b1 等 7 个
                # hand)。架构改正:低桩证据走 diag,高桩(text OCR 直采 / P3
                # 与 text aligned)才进主表。
                if event.confidence_score < 0.7:
                    diag.emit(
                        "action.low_tier_skip",
                        {
                            "player": event.player_name,
                            "position": event.position.value if event.position else "",
                            "street": _street_str,
                            "action": _action_str,
                            "amount": event.amount,
                            "confidence": event.confidence_score,
                            "stack_before": event.raw_data.get("stack_before"),
                            "stack_after": event.raw_data.get("stack_after"),
                            "stack_delta": event.raw_data.get("stack_delta"),
                            "pot_delta": event.raw_data.get("pot_delta"),
                            "action_text": event.raw_data.get("action_text"),
                            "override_reason": event.raw_data.get("override_reason"),
                        },
                        hand_id=self.tracker.current_hand.id,
                    )
                    continue

                # all-in 写库分层(2026-06-11,#243 收尾):检测即标记(内存 mark/diag = 实时档,
                # 上面已设 _went_all_in_this_hand),【写库】推迟到手末过闸(_flush_pending_allins:
                # ②本手有事件 ③归零持续到结算未 broken)。复盘/画像吃的是耐久记录,手末确认零
                # 延迟成本;实时建议(后期)订阅内存 mark,与四闸无关。POKEMIR_ALLIN_DEFER=0 回旧行为。
                if final_action == ActionType.ALL_IN and ALLIN_DEFER_WRITE:
                    if event.amount is None and az_allin_amount is not None:
                        # 金额=本街累计口径(与 bet/call/raise 一致):此前投入 + 全推的剩余栈
                        event.amount = self.tracker._seat_street_amt.get(sidx, 0.0) + az_allin_amount
                    if event.amount is not None:
                        # 复盘补漏(2026-06-11):shove 抬本街地板 — 后续跟注者的 settle 显示
                        # ≥ shove 额,地板不抬则其首帧陈旧值(如自己旧加注额)可能漏网(1202手
                        # BB call 516 读回 258 即此型,A″ 只防"有过本街投入"的座)。即便此
                        # all-in 手末被 veto,地板虚高也只让后续读数多等到 cap 提交真值,无丢失。
                        self.tracker._street_amt_max = max(self.tracker._street_amt_max, event.amount)
                        self.tracker._seat_street_amt[sidx] = max(
                            self.tracker._seat_street_amt.get(sidx, 0.0), event.amount)
                    self._allin_pending[sidx] = event
                    diag.emit("all_in.write_deferred",
                              {"seat": sidx, "amount": event.amount},
                              hand_id=self.tracker.current_hand.id)
                elif db is not None:
                    _pt = time.perf_counter()
                    self.event_repo.create(db, event)
                    sub_ms["seat_parse_persist"] += (time.perf_counter() - _pt) * 1000.0

                # A′/A″:过门(高桩)事件金额抬两级地板 — 低桩 skip/dedup skip 不算(dedup 同值已计)
                if _needs_amt and event.amount is not None:
                    self.tracker._street_amt_max = max(self.tracker._street_amt_max, event.amount)
                    self.tracker._seat_street_amt[sidx] = max(
                        self.tracker._seat_street_amt.get(sidx, 0.0), event.amount)

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

        sub_ms["seat_loop"] = (time.perf_counter() - _loop_t0) * 1000.0  # 整循环体(=gap+在循环OCR)
        # T57(2026-05-29):传 sub_ms 给 _tick 用,merge 进 phase_ms.
        self.tracker._seat_subphase_ms = sub_ms

    def _pot_label_color(self, pot_img):
        """总底池颜色检测计数(2026-06-10,替 phash):实测总底池=高饱和青字、数字=白字。
        white=白数字核(S<60&V>180,数字多/总底池0);teal=亮饱和青(V>120&64≤S≤215,总底池字/数字少)。
        返回 (white, teal)。阈值由 live pot.label_measure 分布定后再判。"""
        hsv = cv2.cvtColor(pot_img, cv2.COLOR_BGR2HSV)
        S, V = hsv[..., 1], hsv[..., 2]
        white = int(((S < 60) & (V > 180)).sum())
        teal = int(((V > 120) & (S >= 64) & (S <= 215)).sum())
        return white, teal

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
        # 2026-06-10:pot 数字走 digit 配方 + 砍 EasyOCR 兜底(13/13污染帧都是兜底硬编数,配方正确返None)。
        # 配方开:读不出=本帧无读(None,不回落)→ 下方防抖 hold。**总底池/结算改【颜色】判定(非文字OCR)**。
        # 配方关(POKEMIR_DIGIT_RECIPE_LIVE=0):退回旧 EasyOCR 全路径(休眠安全网)。
        amount = None
        pot_text = None
        if DIGIT_RECIPE_LIVE and self._digit_reader is not None:
            _v = self._reader_for("pot").read(pot_img)  # zone="pot"(_pot.json);旧"pot_size"查不到退回stack
            if _v is not None:
                amount = float(_v)
        else:
            pot_text = self.ocr.read_text(pot_img)
            amount = ActionRecognizer._extract_amount(pot_text)

        # 信源验证旁路抽头(约束①⑤):抽原始逐帧读 amount(配方,None=未读);采样去重在 LabelCapturer 内。
        if self._labeler.enabled:
            self._labeler.tap("pot_size", pot_img, amount, raw_text=pot_text,
                              wide_frame=self.capturer.get_cached_frame(), roi=rois.pot_size,
                              hand_id=(self.tracker.current_hand.id if self.tracker.current_hand else None))

        # 上街池 potprev 信源验证(LABEL_SIGNAL=potprev):live production 本不读它,这里读+tap 供验证
        # (验证专用,不改 production 行为)。配方 zone="potprev"(无专模板回退 stack)。
        if self._labeler.enabled and "potprev" in self._labeler.signals and rois.pot_size_previous is not None:
            _pp_img = self.capturer.capture_roi(rois.pot_size_previous)
            _pp = None
            if (DIGIT_RECIPE_LIVE and self._digit_reader is not None
                    and _pp_img is not None and _pp_img.size > 0):
                _ppv = self._reader_for("potprev").read(_pp_img)
                if _ppv is not None:
                    _pp = float(_ppv)
            self._labeler.tap("potprev", _pp_img, _pp,
                              wide_frame=self.capturer.get_cached_frame(), roi=rois.pot_size_previous,
                              hand_id=(self.tracker.current_hand.id if self.tracker.current_hand else None))

        # 总底池颜色【判定】(2026-06-10,18/18验收,替原文字 OCR 开手):配方读空时量 white(白数字核)/
        # teal(亮饱和青)。white<阈 且 teal≥阈 = 结算帧(总底池显示)→ 每手 latch 一次 emit pot.label_detected。
        # 实测:总底池=高饱和青字(white≈0 teal403-530)、数字=白(white高)、空/暗(both低),正交可分。
        # (旧"总底池"文字 OCR 开手块已删:BUTTON_CUT 权威切手、该路 75min/94手 fire=0=纯死代码。)
        # 用途(收手标记/摊牌读取窗)待②单独接 showdown;本步仅 latch + emit,不改行为。
        if amount is None and not self._pot_label_latched:
            _w, _tl = self._pot_label_color(pot_img)
            if _w < POT_LABEL_WHITE_TH and _tl >= POT_LABEL_TEAL_TH:
                self._pot_label_latched = True
                _hid = self.tracker.current_hand.id if self.tracker.current_hand else None
                diag.emit("pot.label_detected", {"white": _w, "teal": _tl,
                          "hand_id": str(_hid) if _hid else None}, hand_id=_hid)

        # 防抖(2026-06-10):同值连续≥2帧才接受 → 杀单帧动画毛刺 + 尖峰(治 spike-lock:单帧 13353
        # 到不了 2 帧、永不被接受、也就不会锁死后续正确读)。None=本帧无读 → hold 上一池值。
        amount = pot_debounce_step(self._pot_debounce, amount)
        if amount is None:
            return

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

    def _detect_blind_levels(self) -> None:
        """T17(2026-05-28)+ T31 fix:抓 SB(button+1)/ BB(button+2)的 amount_area。

        WePoker 每手开始时 SB/BB 已被强制扣盲注,amount_area 显示金额(类似 2/4)。
        但**只在 preflop 没结束时**抓才准 — 一旦 flop 发牌,SB/BB 的 amount_area
        被 voluntary action(call/raise/fold)的最新数字覆盖,不再是 blind。

        T31 fix:check community_cards 是否已发,observer 模式 _start_new_hand
        触发时 community 已 ≥ 3,此时跳过(blind 抓不到,数据已被覆盖)。

        坐下模式正常工作(_start_new_hand 触发于 hero cards 出现 = preflop 早期)。

        落 hand.raw_data['blind_level'] = {'sb': X, 'bb': Y}。

        依赖:T13 button_seat_index 准确;T24 amount_area ROI 收窄。
        """
        if self.tracker.current_hand is None:
            return
        # T31 fix:community 已发(observer 模式)→ blind 数据已被覆盖,放弃
        from events.models import Street
        cc = self.tracker.current_hand.community_cards or {}
        for street in (Street.FLOP, Street.TURN, Street.RIVER):
            cards = cc.get(street) or []
            if len(cards) > 0:
                diag.emit(
                    "blind.skip_community_already_dealt",
                    {"street": street.value, "cards_count": len(cards)},
                    hand_id=self.tracker.current_hand.id,
                )
                return
        button_seat = self.roi_manager.button_seat_index
        if button_seat is None:
            return
        num_seats = self.roi_manager.rois.num_seats or len(self.roi_manager.rois.seat_regions)
        if num_seats < 2:
            return

        sb_idx = (button_seat + 1) % num_seats
        bb_idx = (button_seat + 2) % num_seats

        blinds: dict[str, float] = {}
        for label, idx in [("sb", sb_idx), ("bb", bb_idx)]:
            seat = next((s for s in self.roi_manager.rois.seat_regions if s.seat_index == idx), None)
            if seat is None or seat.amount_area is None or seat.amount_area.width == 0:
                continue
            img = self.capturer.capture_roi(seat.amount_area)
            if img.size == 0:
                continue
            text = self.ocr.read_text(img, allowlist="0123456789.")
            if not text:
                continue
            # 提取首个数字(可能有 OCR noise 如 "2." / " 4")
            import re as _re
            m = _re.search(r"(\d+\.?\d*)", text)
            if m:
                try:
                    value = float(m.group(1))
                    if 0 < value < 100000:  # sanity range
                        blinds[label] = value
                except ValueError:
                    pass

        if blinds:
            if self.tracker.current_hand.raw_data is None:
                self.tracker.current_hand.raw_data = {}
            self.tracker.current_hand.raw_data["blind_level"] = blinds
            logger.info(f"[T17] blind_level detected: sb_seat={sb_idx} bb_seat={bb_idx} → {blinds}")
            diag.emit(
                "blind.detected",
                {"sb_seat": sb_idx, "bb_seat": bb_idx, "blinds": blinds, "button_seat": button_seat},
                hand_id=self.tracker.current_hand.id,
            )

    def _read_table_blinds(self):
        """桌规录入:优先 env(POKEMIR_SB/BB/ANTE),否则交互 prompt(仅 tty)。
        返回 {'sb','bb','ante'} 或 None(未提供 → 回落 OCR 盲注检测,不阻塞 replay/cron)。"""
        import sys as _sys

        def _f(k):
            v = os.getenv(k)
            try:
                return float(v) if v not in (None, "") else None
            except ValueError:
                return None
        sb, bb, ante = _f("POKEMIR_SB"), _f("POKEMIR_BB"), _f("POKEMIR_ANTE")
        if sb is None and bb is None and getattr(_sys.stdin, "isatty", lambda: False)():
            try:
                print("【桌规录入】直接回车跳过(回落 OCR 盲注检测):")
                s = input("  小盲 SB = ").strip()
                if s:
                    sb = float(s)
                    bb = float(input("  大盲 BB = ").strip())
                    a = input("  前注 ante(无则回车=0)= ").strip()
                    ante = float(a) if a else 0.0
            except (EOFError, ValueError, KeyboardInterrupt):
                return None
        if sb is None or bb is None:
            return None
        return {"sb": sb, "bb": bb, "ante": ante or 0.0}

    @staticmethod
    def _avg_hash_int(crop):
        """8x8 average-hash → 64-bit int。**忠实复制 replay_reconstruct._avg_hash**(profile 里
        card_marker_ref 就是它算的,必须同算法才可比;BGRA→GRAY 适配 mss 4 通道)。⚠️ Win-only。"""
        import cv2
        if crop is None or getattr(crop, "size", 0) == 0:
            return 0
        code = cv2.COLOR_BGRA2GRAY if (crop.ndim == 3 and crop.shape[2] == 4) else cv2.COLOR_BGR2GRAY
        g = cv2.resize(cv2.cvtColor(crop, code), (8, 8))
        m = float(g.mean())
        bits = 0
        for v in g.flatten():
            bits = (bits << 1) | (1 if float(v) > m else 0)
        return bits

    @staticmethod
    def _hamming64(a, b):
        return bin(int(a) ^ int(b)).count("1")

    def _detect_active_set(self, th=8, emit=True):
        """精确活跃集(2026-06-06,#228 接 live):每座 card_marker(头像左下两红牌背)avg_hash 对
        持久参考 card_marker_ref hamming≤th → 在手。治"占座≠发牌"(带入审核/坐下未发)。
        返回在手座 set。无 card_marker/ref 的座跳过。emit=False 供 per-tick 调用(改走 on-change 诊断,
        防每 tick 刷库)。⚠️ cv2 路径 Win-only 未验。"""
        active, cands = set(), []
        for seat_roi in self.roi_manager.rois.seat_regions:
            if seat_roi.card_marker is None or seat_roi.card_marker_ref is None:
                continue
            img = self.capturer.capture_roi(seat_roi.card_marker)
            if img.size == 0:
                continue
            ham = self._hamming64(self._avg_hash_int(img), seat_roi.card_marker_ref)
            cands.append((seat_roi.seat_index, ham))
            if ham <= th:
                active.add(seat_roi.seat_index)
        # 诊断(前3次):card_marker 各座 hamming(≤th=在手)。全>th=参考对不上这桌牌背 → 活跃集瘫根因
        self._cm_dbg = getattr(self, "_cm_dbg", 0)
        if self._cm_dbg < 3 and cands:
            self._cm_dbg += 1
            logger.info(f"[活跃集诊断] card_marker hammings(≤{th}=在手): {cands} → 在手座 {sorted(active)}"
                        f"{'  ⚠️全部>阈=参考不匹配' if not active else ''}")
        if emit:
            diag.emit("active_set.detected", {"active": sorted(active), "hammings": cands, "th": th},
                      hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None)
        return active

    def _detect_showdown_corner(self):
        """#235 摊牌闸:每 tick 【无条件】检 showdown_corner 白角(所有有该框的座,**不受活跃集/skip**
        — 治"牌背消失→剔活跃集→不再看→漏摊牌信号"那个 race)。白计数(S<=60 & V>=180)≥
        SHOWDOWN_WHITE_TH = 该座亮牌。返回亮牌座 set。18001帧验:摊牌81-156 vs 非摊牌≤20。⚠️ cv2 Win-only。"""
        showing = set()
        for seat_roi in self.roi_manager.rois.seat_regions:
            roi = seat_roi.showdown_corner
            if roi is None or getattr(roi, "width", 0) < 2:
                continue
            img = self.capturer.capture_roi(roi)
            if img is None or img.size == 0:
                continue
            bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR) if img.shape[2] == 4 else img
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            if int(((hsv[..., 1] <= 60) & (hsv[..., 2] >= 180)).sum()) >= SHOWDOWN_WHITE_TH:
                showing.add(seat_roi.seat_index)
        return showing

    def _scan_button_white_frac(self, white_th=170, frac_th=0.12):
        """白占比 argmax 扫各座 button_indicator → (button_seat|None, candidates)。
        供 per-hand 定位(_detect_button_position)+ per-tick 切手(2b)共用。
        忠实复制 replay_reconstruct._white_frac:三通道都 > white_th 的像素占比,取最白座,
        超 frac_th 才认否则 None。mss=BGRA 4 通道 alpha=255≥th 不改 min,与夹具 BGR 同结果。
        ⚠️ cv2 路径 Win-only 未验。"""
        candidates = []  # (seat_index, white_frac, brightness)
        best_seat, best_wf = None, 0.0
        for seat_roi in self.roi_manager.rois.seat_regions:
            if seat_roi.button_indicator is None:
                continue
            img = self.capturer.capture_roi(seat_roi.button_indicator)
            if img.size == 0 or img.ndim != 3:
                continue
            wf = float((img.min(axis=2) > white_th).mean())
            candidates.append((seat_roi.seat_index, round(wf, 3), round(float(img.mean()), 1)))
            if wf > best_wf:
                best_seat, best_wf = seat_roi.seat_index, wf
        return (best_seat if best_wf > frac_th else None), candidates

    def _predict_next_button(self):
        """#242 飞行窗预测:上一手按钮 → 顺时针【下一占位座】(必跳空座)。

        按钮每手顺时针移到下一个【在座】玩家(空座不发牌不接按钮)→ 占用表确定下一座。
        依赖占用判定:无上一手按钮 / 占用不可用 / 无占位座 → 返 None(不盲猜,免落空座致盲注错;
        用户明示『+1 必须排除空座』)。返回预测的 seat_index 或 None。纯几何 + 占用,无 OCR。"""
        if self._last_button_seat is None:
            return None
        occ = self._classify_occupancy()
        if not occ:
            return None  # 占用未启用/基线缺 → 无法跳空座 → 诚实 None
        occupied = {s for s, v in occ.items() if v.get("occupied")}
        if not occupied:
            return None
        num = self.roi_manager.rois.num_seats
        for k in range(1, num + 1):
            cand = (self._last_button_seat + k) % num
            if cand in occupied:
                return cand
        return None

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
        # 2026-06-06:改用【白占比 argmax】定 D 座(忠实复制 replay_reconstruct._white_frac +
        # build_series_real:152-158,white_th=170/frac_th=0.12)。原 L1=OCR"D"第一个命中在
        # live 把 seat0 假"D"短路成永远 seat0(多人桌实测 27 手只在 0/2,见 button.detected),
        # 而夹具/T139 用的正是白占比(不OCR)且验过稳 → 验过的方法没上 live(同 amount 模板坑)。
        button_seat, candidates = self._scan_button_white_frac()
        # #242 埋点(verify 用):并存两路按钮座 — 单帧白占比原始读 raw_scan_seat(可能 None)
        # vs BUTTON_CUT 去抖确认值 _btn_confirmed。满桌该恒 +1,下次干净录制比哪路给干净 +1
        # 序列(白占比单帧 50% 错/含 delta=0 不可能跳;去抖结构上只产顺时针单调)→ 数据定夺优先级。
        raw_scan_seat = button_seat
        confirmed_seat = self._btn_confirmed
        if button_seat is not None:
            method = "white-frac"
            logger.info(f"Button detected at seat {button_seat} via white-frac")
        elif self._btn_confirmed is not None:
            # #242 兜底①:白占比这帧没看清 D(飞行动画/遮挡)→ 用 BUTTON_CUT 在线去抖
            # 已【确认落座】的 _btn_confirmed。换手正是它触发的(button_move_online moved=True
            # 即把 confirmed 推进到新座)→ 它就是本手权威按钮座,不该被重扫的 None 覆盖。
            # 这是 51/84 NULL 的主因:重扫赶上飞行窗,而权威值白白没用。
            button_seat = self._btn_confirmed
            method = "btn-confirmed"
            logger.info(f"Button white-frac None → 用 _btn_confirmed seat {button_seat}(#242 兜底①)")
        else:
            # #242 兜底②:连 _btn_confirmed 都没有(首手/整局从未确认)→ 顺时针下一占位座预测
            # (靠占用表跳空座)。占用不可用 / 无上一手按钮 → 仍诚实 None(不盲猜落空座致盲注错)。
            pred = self._predict_next_button()
            if pred is not None:
                button_seat = pred
                method = "predict-clockwise"
                logger.info(f"Button white-frac None & 无 confirmed → 预测顺时针下一占位座 {button_seat}(#242 兜底②)")
            else:
                button_seat = None
                method = "none"
                logger.warning(f"Button white-frac all below threshold & 无兜底 → None. Candidates: {candidates}")

        # T13 diag:每手记录 button 检测方法 + 候选,便于事后审视
        diag.emit(
            "button.detected",
            {
                "button_seat": button_seat,
                "method": method,
                "white_frac_seat": raw_scan_seat,   # #242 埋点:单帧白占比原始读
                "confirmed_seat": confirmed_seat,    # #242 埋点:去抖确认座
                "candidates": [{"seat": c[0], "white_frac": c[1], "brightness": c[2]} for c in candidates],
            },
            hand_id=self.tracker.current_hand.id if self.tracker.current_hand else None,
            level="WARN" if method == "none" else "INFO",  # #242:兜底①②是有效填补,非失败
        )

        self.roi_manager.button_seat_index = button_seat
        if button_seat is not None:
            self._last_button_seat = button_seat  # #242 预测基:任一方法定下都更新,下手预测/落座确认前向纠错
        # T13:把 button_seat_index 落 hand.raw_data,便于 audit / dashboard 用
        if self.tracker.current_hand is not None:
            if self.tracker.current_hand.raw_data is None:
                self.tracker.current_hand.raw_data = {}
            self.tracker.current_hand.raw_data["button_seat_index"] = button_seat
            self.tracker.current_hand.raw_data["button_detection_method"] = method
            # #242 埋点:两路并存入库,供 live 比对哪路给干净 +1(满桌)→ 定按钮优先级
            self.tracker.current_hand.raw_data["button_white_frac_seat"] = raw_scan_seat
            self.tracker.current_hand.raw_data["button_confirmed_seat"] = confirmed_seat

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
