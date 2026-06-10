"""Central configuration — reads from environment variables with sensible defaults."""

import os
import logging

from dotenv import load_dotenv

load_dotenv()   # auto-load project-root .env so os.getenv() picks up user-set values
                # (.env is gitignored; .env.example is the template)

# ── Database ──────────────────────────────────────────────
DB_DSN = os.getenv(
    "POKEMIR_DB_DSN",
    "postgresql+asyncpg://poker_user:poker_pass@localhost:5432/poker_assistant",
)
DB_DSN_SYNC = os.getenv(
    "POKEMIR_DB_DSN_SYNC",
    "postgresql://poker_user:poker_pass@localhost:5432/poker_assistant",
)

# ── Recognition ───────────────────────────────────────────
MODEL_DIR = os.getenv("POKEMIR_MODEL_DIR", "./models")
ONNX_DEVICE = os.getenv("POKEMIR_ONNX_DEVICE", "DirectML")
OCR_ENGINE = os.getenv("POKEMIR_OCR_ENGINE", "easyocr")
EASYOCR_MODEL_DIR = os.getenv("POKEMIR_EASYOCR_DIR", os.path.join(MODEL_DIR, "easyocr"))
# T72(2026-05-29):GPU OCR 开关.默认 False(向后兼容 CPU 模式).
# 启用前提:torch.cuda.is_available() = True
#   Win 5070 Ti Blackwell sm_120 需 PyTorch cu128 wheel.
#   EasyOCR 内部 gpu=True 自动用 CUDA.
# 启用方式:`POKEMIR_USE_GPU=1` env var(.env 或 shell).
USE_GPU = os.getenv("POKEMIR_USE_GPU", "0").lower() in ("1", "true", "yes")
# T73(2026-05-29):Batch OCR 开关.GPU 模式下 readtext_batched 8 seat × 5 ROI 一次 GPU launch.
# 预期 OCR 总耗时 ~1.4s → ~200ms,tick 4.9s → 2.5-3s.
# 启用方式:POKEMIR_OCR_BATCH=1.前提 USE_GPU=1 才有意义.
OCR_BATCH = os.getenv("POKEMIR_OCR_BATCH", "0").lower() in ("1", "true", "yes")
# (2026-06-06 P3 退役:ATTENTION_MODE 双OCR/attention 实验整套删除——从未上 live、用户判定弃。)
# 2026-06-01:spent-investigation 探针总开关(fold_probe / p3.text_priority /
# showdown.dark_cards_area 等调查已结束的 observability)。默认关 = 不写 DB,
# 减 transition 尖刺 + DB clutter。需要时 POKEMIR_VERBOSE_DIAG=1 重新打开。
VERBOSE_DIAG = os.getenv("POKEMIR_VERBOSE_DIAG", "0").lower() in ("1", "true", "yes")
# 2026-06-01 spike A:把逐座 timer/fold_text/fold_area OCR 批成 3 次 GPU call
# (原 ~24 次独立调用 = seat_actions 真瓶颈,GPU 异步下被计时器低估)。默认关
# 2026-06-01 A/B 验证:tick 0.32→1.08Hz、pot-gap 丢失 29.4%→18.5% → 默认开。
# 回退:POKEMIR_BATCH_SEAT_OCR=0(逐座旧路径)。
BATCH_SEAT_OCR = os.getenv("POKEMIR_BATCH_SEAT_OCR", "1").lower() in ("1", "true", "yes")
# (2026-06-06 P3 退役:STACK_PROBE stack-drop 探针、SHADOW_POINTER T48 指针扫描 均删——
#  纯实验探针/gated 默认关、从未切主链路。)
# 2026-06-05 杠杆A:live 热路径 stack 读取走【数字配方】(DigitReader,模板匹配,比 EasyOCR
# 快~10×,bench 实测)而非 EasyOCR。配方主读、读空(全下%/不可读)回落 EasyOCR(认 %=all-in)。
# 需 rois/digit_templates_<profile>.json(build_digit_templates.py 产)。默认【关】零行为变化;
# POKEMIR_DIGIT_RECIPE_LIVE=1 开。先 stack,验准/提速后再推 amount。
DIGIT_RECIPE_LIVE = os.getenv("POKEMIR_DIGIT_RECIPE_LIVE", "0").lower() in ("1", "true", "yes")
# 2026-06-05 杠杆D.1:每 tick 抓【一次整窗】存缓存,capture_roi 从缓存切片,替掉~37 次
# 独立 mss grab(Stage0 实测 seat_actions 里 ~900ms 未计时=这些 grab)。像素字节级等价、
# 行为不变。2026-06-09:默认【开】——tools/verify_frame_capture.py 实测真窗口 108/108 ROI
# 字节级一致(空桌负坐标都对齐);live 验证 capture_grab 154ms→capture_frame 12ms、无质量回归
# (公共牌 jitter 8.7% 是旧基线非本改引入)。POKEMIR_FRAME_CAPTURE=0 可回退逐ROI grab。
FRAME_CAPTURE = os.getenv("POKEMIR_FRAME_CAPTURE", "1").lower() in ("1", "true", "yes")

# 2026-06-06 step 2b:按钮权威切手(观战模式)。每 tick 白占比扫按钮 → 顺时针单调+在线去抖,
# 确认 D 移座 = 换手(reconstruct.button_move_online)。开时:换手只认按钮 + "总底池"兜底,
# 关掉 hero换牌/公共牌reset 触发(治公共牌 reset 过切——实测 btn=5 连两手=一手被劈两半)。
# 2026-06-08:默认【开】。按钮检测已 live 验收(整圈轮转零过切,见 hand-segmentation-corroboration);
# 优于公共牌reset切手(治"一手劈多手"过切)。POKEMIR_BUTTON_CUT=0 可回退到公共牌reset切手。
BUTTON_CUT = os.getenv("POKEMIR_BUTTON_CUT", "1").lower() in ("1", "true", "yes")
# 2026-06-07 #237:recognize-only OCR 路径。EasyOCR `reader.recognize(整帧灰度图,固定框)`
# 跳过最贵的 CRAFT 检测阶段(控制 bench: C=559ms vs A 逐ROI readtext=1750ms,×3.1)。
# 与 T52 diff 缓存【叠加】:只对"变了的座"用、整帧灰度图每 tick 算一次。默认【关】零变化;
# POKEMIR_OCR_RECOGNIZE_ONLY=1 开。【依赖 FRAME_CAPTURE=1】(需缓存整帧);无缓存帧/坐标越界
# 自动回退 read_text。准度 Win 验(bench 只测速)。详见 requirement-discussions/
# 2026-06-06_capture-frequency-decouple-batch-throughput.md §2/§9。
OCR_RECOGNIZE_ONLY = os.getenv("POKEMIR_OCR_RECOGNIZE_ONLY", "0").lower() in ("1", "true", "yes")
# 2026-06-07 #240:动作识别走 text-shape phash 二元桩(色盲+位置无关归一化文字形状,替 action OCR)。
# 每座 action_area → 抠白字 → bbox归一 → 8×8 aHash → 比 rois/action_refs_<profile>.json 参考。
# 治:下注实测多色 + 各座ROI相对位置不一 + idle 噪声。需先 tools/build_action_refs.py 建参考文件。
# 默认【开】(2026-06-07 live 验收:四动作全绿+加/下死结解开+phash读字×游戏状态双保险,
# 12 处 text≠stack 全是 phash 纠正 stack 噪声、phash 零误词)。POKEMIR_ACTION_PHASH_LIVE=0 关回 OCR;
# 参考文件缺(他桌型)自动回退 OCR,零风险。详见 requirement-discussions/2026-06-07_action-recognition-text-shape-phash.md。
ACTION_PHASH_LIVE = os.getenv("POKEMIR_ACTION_PHASH_LIVE", "1").lower() in ("1", "true", "yes")

# 2026-06-08 #241(rebuy 前置):每座占用判定 = live 区域 avg_hash vs 空桌基线
# (rois/empty_refs_<profile>.json,build_empty_refs.py 采)。hamming ≤ 阈 → 像空桌=空座;
# > 阈 → 占位。喂 rebuy 的"爆码座 absent→present"判定。默认【关】,缺参考文件自动跳过。
SEAT_OCCUPANCY_LIVE = os.getenv("POKEMIR_SEAT_OCCUPANCY", "0").lower() in ("1", "true", "yes")
# 2026-06-09 #243 第一步:all-in stack→0 稳定信号【影子检测】(diag-only,不改动作发射)。
# 离线两段录像验收召回100%;本步 live 旁路 emit `all_in.stack_zero`(座/金额/活跃),
# 录长局后与现有 OCR all-in 交叉印证 → 验过再让它当家 + 砍 fold OCR。去伪主闸=card_marker
# 活跃集(本手持牌,非raw occupancy)。默认【开】(纯 diag,零行为变化);=0 关。
ALLIN_STACKZERO = os.getenv("POKEMIR_ALLIN_STACKZERO", "1").lower() in ("1", "true", "yes")
ALLIN_ZERO_RUN = int(os.getenv("POKEMIR_ALLIN_ZERO_RUN", "2"))  # 判稳定的连续 0 读帧数(实测单帧0噪声=0,2 即稳)

HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
VISION_MODEL = os.getenv("POKEMIR_VISION_MODEL", "HuggingFaceTB/SmolVLM-256M-Instruct")

# ── Capture ───────────────────────────────────────────────
# 2026-06-09:默认 250→30。FRAME_CAPTURE 后 tick~180ms,250 sleep 白卡帧率(1.8hz);降 30
# → ~4.8hz(实测 sleep=0 跑 5.5hz/切手正常/CPU 扛得住/无质量回归,30 更稳不满核空转)。
# ⚠️ 部分防抖按 tick 数算(按钮去抖/每4tick强刷),低 sleep 下真实时间缩~2.6× — 切手已实测
# 扛住,但锁更低(→0)前应审 tick-based 计时是否需改墙钟。POKEMIR_CAPTURE_INTERVAL_MS 可调。
CAPTURE_INTERVAL_MS = int(os.getenv("POKEMIR_CAPTURE_INTERVAL_MS", "30"))

# ── ROI ──────────────────────────────────────────────────
ROI_CONFIG_DIR = os.getenv("POKEMIR_ROI_DIR", "./rois")
ROI_PROFILE = os.getenv("POKEMIR_ROI_PROFILE", "party_poker_9")

# ── Stats ─────────────────────────────────────────────────
ROLLING_WINDOW_HANDS = int(os.getenv("POKEMIR_ROLLING_WINDOW", "50"))

# ── Detection ─────────────────────────────────────────────
HASH_THRESHOLD = int(os.getenv("POKEMIR_HASH_THRESHOLD", "10"))

# ── API ───────────────────────────────────────────────────
API_HOST = os.getenv("POKEMIR_API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("POKEMIR_API_PORT", "8765"))

# ── Logging ───────────────────────────────────────────────
LOG_LEVEL = os.getenv("POKEMIR_LOG_LEVEL", "INFO")
LOG_DIR = os.getenv("POKEMIR_LOG_DIR", "./logs")
_log_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_log_datefmt = "%H:%M:%S"

# Console handler — stderr, terse time format
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter(_log_fmt, datefmt=_log_datefmt))

# File handler — per-day rotation, full timestamp for cross-session grep.
# logs/ is gitignored (*.log).  Failure to create is non-fatal — falls back
# to console-only so the pipeline still runs on read-only/sandboxed FS.
_handlers = [_console]
try:
    os.makedirs(LOG_DIR, exist_ok=True)
    from logging.handlers import TimedRotatingFileHandler
    from datetime import datetime
    _log_path = os.path.join(LOG_DIR, f"pokemir_{datetime.now():%Y-%m-%d}.log")
    _file = TimedRotatingFileHandler(_log_path, when="midnight", backupCount=14, encoding="utf-8")
    _file.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    _handlers.append(_file)
except OSError as _e:
    print(f"[config] WARN: log file disabled ({_e!r}) — console only", flush=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    handlers=_handlers,
    force=True,
)
