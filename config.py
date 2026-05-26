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
HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
VISION_MODEL = os.getenv("POKEMIR_VISION_MODEL", "HuggingFaceTB/SmolVLM-256M-Instruct")

# ── Capture ───────────────────────────────────────────────
CAPTURE_INTERVAL_MS = int(os.getenv("POKEMIR_CAPTURE_INTERVAL_MS", "500"))

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
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
