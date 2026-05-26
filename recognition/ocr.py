"""OCR engine wrapper for reading text from poker table ROIs."""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class OCREngine:
    """Wraps EasyOCR for reading action text, amounts, and card ranks.

    Pre-processes images with upscale + thresholding to improve
    accuracy on small / anti-aliased fonts.
    """

    def __init__(self, gpu: bool = False):
        self._reader = None
        self._gpu = gpu

    def _init(self):
        if self._reader is not None:
            return
        import os
        import easyocr
        from config import EASYOCR_MODEL_DIR
        os.makedirs(EASYOCR_MODEL_DIR, exist_ok=True)
        user_network_dir = os.path.join(EASYOCR_MODEL_DIR, "user_network")
        os.makedirs(user_network_dir, exist_ok=True)
        # 'ch_sim' enables WePoker Chinese action text (跟注/加注/弃牌/...);
        # 'en' kept for card rank glyphs + numeric amounts. First-time loading
        # auto-downloads ~50MB ch_sim model to POKEMIR_EASYOCR_DIR (.cache/easyocr/).
        self._reader = easyocr.Reader(
            ["ch_sim", "en"],
            gpu=self._gpu,
            model_storage_directory=EASYOCR_MODEL_DIR,
            user_network_directory=user_network_dir,
            download_enabled=True,
        )

    def read_text(self, image: np.ndarray, allowlist: str = "", ensemble: bool = False) -> str:
        """Extract text from an image region. Returns empty string if nothing found.

        Args:
            image: BGR / BGRA crop
            allowlist: if non-empty, restrict OCR output to these characters.
            ensemble: #8 if True, run OCR on TWO preprocessed variants (default
                2x upscaled + 3x upscaled) and pick the longer non-empty result.
                Use sparingly (2x cost); good for action / id where accuracy matters.
        """
        self._init()
        if not ensemble:
            return self._read_one(image, allowlist, scale=2)
        # Ensemble: try 2x and 3x scales, prefer longer result
        result_2x = self._read_one(image, allowlist, scale=2)
        result_3x = self._read_one(image, allowlist, scale=3)
        if result_2x == result_3x:
            return result_2x
        # Prefer longer (more chars likely captured); empty falls back to other
        if not result_2x:
            return result_3x
        if not result_3x:
            return result_2x
        return result_2x if len(result_2x) >= len(result_3x) else result_3x

    def _read_one(self, image: np.ndarray, allowlist: str, scale: int) -> str:
        processed = self._preprocess(image, scale=scale)
        try:
            kwargs = {"detail": 0}
            if allowlist:
                kwargs["allowlist"] = allowlist
            results = self._reader.readtext(processed, **kwargs)
        except Exception:
            logger.warning("OCR call failed", exc_info=True)
            return ""
        return " ".join(results).strip()

    def _preprocess(self, image: np.ndarray, scale: int = 2) -> np.ndarray:
        """Upscale, grayscale, threshold to improve OCR accuracy."""
        if image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

        h, w = image.shape[:2]
        # Upscale (configurable scale for #8 ensemble)
        image = cv2.resize(image, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return thresh
