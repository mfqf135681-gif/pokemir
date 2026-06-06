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
# Phase 1.5 v3.2 (2026-05-31 T89):注意力机制 + 双 OCR + Seat 5/Hand 12 状态机
# + §12 摊牌专项 + 13 规则盲点.Step 1 of 9-step execution sequence
# (详 requirement-discussions/2026-05-30_phase-1-5-attention-mechanism-design.md
#  §11.4). 默认 0 = 旧 path 100% 不变 (T80/T82 模块躺仓库不集成).
# 后续 Step 2-9 实施时,所有新 path 包 `if ATTENTION_MODE:` 守卫,旧 path
# 保 fallback. **回滚靠 env var 而非双轨代码** (per §11.3 陷阱 4).
# 启用方式:POKEMIR_ATTENTION_MODE=1.前提 USE_GPU=1 + OCR_BATCH=1 已生效.
ATTENTION_MODE = os.getenv("POKEMIR_ATTENTION_MODE", "0").lower() in ("1", "true", "yes")
# 2026-06-01:spent-investigation 探针总开关(fold_probe / p3.text_priority /
# showdown.dark_cards_area 等调查已结束的 observability)。默认关 = 不写 DB,
# 减 transition 尖刺 + DB clutter。需要时 POKEMIR_VERBOSE_DIAG=1 重新打开。
VERBOSE_DIAG = os.getenv("POKEMIR_VERBOSE_DIAG", "0").lower() in ("1", "true", "yes")
# 2026-06-01 spike A:把逐座 timer/fold_text/fold_area OCR 批成 3 次 GPU call
# (原 ~24 次独立调用 = seat_actions 真瓶颈,GPU 异步下被计时器低估)。默认关
# 2026-06-01 A/B 验证:tick 0.32→1.08Hz、pot-gap 丢失 29.4%→18.5% → 默认开。
# 回退:POKEMIR_BATCH_SEAT_OCR=0(逐座旧路径)。
BATCH_SEAT_OCR = os.getenv("POKEMIR_BATCH_SEAT_OCR", "1").lower() in ("1", "true", "yes")
# 2026-06-01 stack-drop 探针:每 tick 记 stack 下跌(规则:筹码守恒,stack 掉=投了钱
# =一个 chip 动作)。验证"stack 是否比 timer/动作字幕更可靠地逮住漏掉的动作"。
# 默认关;POKEMIR_STACK_PROBE=1 测时开。验证通过后再考虑扶正为主驱动。
STACK_PROBE = os.getenv("POKEMIR_STACK_PROBE", "0").lower() in ("1", "true", "yes")
# 2026-06-05:shadow_pointer_scan(T48 指针架构实验扫描)总开关。每 tick 对 8 座 timer
# 各跑一次 EasyOCR(Stage0 实测 ~321ms/tick),但【只 emit diag、未切主链路】=纯实验
# 开销烧在热路径。默认【关】省这 321ms;需采指针候选数据时 POKEMIR_SHADOW_POINTER=1。
SHADOW_POINTER = os.getenv("POKEMIR_SHADOW_POINTER", "0").lower() in ("1", "true", "yes")
HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
VISION_MODEL = os.getenv("POKEMIR_VISION_MODEL", "HuggingFaceTB/SmolVLM-256M-Instruct")

# ── Capture ───────────────────────────────────────────────
CAPTURE_INTERVAL_MS = int(os.getenv("POKEMIR_CAPTURE_INTERVAL_MS", "250"))

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
