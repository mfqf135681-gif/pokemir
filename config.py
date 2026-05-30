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
