"""Vision client for card classification using SmolVLM (Apache 2.0, free).

Falls back gracefully if the model can't load. The CardRecognizer
falls through to color+OCR heuristic when this client returns None.
"""

import logging
import re
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

CARD_PROMPT = (
    "Identify this playing card. Answer with exactly two characters: "
    "rank then suit. "
    "Ranks: 2,3,4,5,6,7,8,9,T,J,Q,K,A. "
    "Suits: s=spades, h=hearts, d=diamonds, c=clubs. "
    "Examples: Ah, Td, Ks. "
    "Answer with ONLY the two characters, no other text."
)

CARD_RE = re.compile(r"[2-9TJQKA][shdc]", re.IGNORECASE)


class VisionClient:
    """SmolVLM-based card classifier.

    Uses HuggingFaceTB/SmolVLM-256M-Instruct — 256M params, <1GB VRAM,
    Apache 2.0 license. Fully local, no API key needed.
    """

    MODEL_ID = "HuggingFaceTB/SmolVLM-256M-Instruct"

    def __init__(self):
        self._model = None
        self._processor = None
        self._available: Optional[bool] = None

    @property
    def available(self) -> bool:
        if self._available is None:
            self._available = self._try_init()
        return self._available

    def _try_init(self) -> bool:
        try:
            import os
            from transformers import AutoModelForImageTextToText, AutoProcessor

            from config import HF_ENDPOINT

            if HF_ENDPOINT:
                os.environ.setdefault("HF_ENDPOINT", HF_ENDPOINT)

            logger.info(f"Loading {self.MODEL_ID}...")
            self._processor = AutoProcessor.from_pretrained(self.MODEL_ID)

            device_map = "cuda:0" if self._has_cuda() else "cpu"
            self._model = AutoModelForImageTextToText.from_pretrained(
                self.MODEL_ID,
                torch_dtype="auto",
                device_map=device_map,
            )
            logger.info(f"SmolVLM loaded on {device_map}")
            return True
        except ImportError:
            logger.info("transformers not installed")
            return False
        except Exception:
            logger.warning("SmolVLM init failed", exc_info=True)
            return False

    def _has_cuda(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False

    def identify_card(self, image: np.ndarray) -> Optional[dict]:
        """Return {"rank": "A", "suit": "h"} or None on failure."""
        if not self.available or self._model is None:
            return None
        try:
            return self._query_and_parse(image)
        except Exception:
            logger.debug("Vision query failed", exc_info=True)
            return None

    def _query_and_parse(self, image: np.ndarray) -> Optional[dict]:
        # Convert numpy BGRA → PIL RGB
        if image.shape[2] == 4:
            image = image[..., :3]
        pil = Image.fromarray(image)
        if pil.mode != "RGB":
            pil = pil.convert("RGB")

        messages = [
            {
                "role": "user",
                "content": [{"type": "image"}, {"type": "text", "text": CARD_PROMPT}],
            },
        ]
        prompt = self._processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs = self._processor(text=prompt, images=[pil], return_tensors="pt")

        device = next(self._model.parameters()).device
        inputs = {k: v.to(device) for k, v in inputs.items()}

        output = self._model.generate(**inputs, max_new_tokens=20)
        response = self._processor.decode(output[0], skip_special_tokens=True)

        # Extract the assistant's part after the prompt
        if "Assistant:" in response:
            response = response.split("Assistant:")[-1]

        return self._parse(response)

    def _parse(self, text: str) -> Optional[dict]:
        if not text:
            return None
        clean = text.strip().upper()
        m = CARD_RE.search(clean)
        if not m:
            logger.debug(f"Vision response not parseable: {clean!r}")
            return None
        code = m.group(0)
        return {"rank": code[0], "suit": code[1].lower()}
