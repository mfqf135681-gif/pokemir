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
        self._reader = easyocr.Reader(
            ["en"],
            gpu=self._gpu,
            model_storage_directory=EASYOCR_MODEL_DIR,
            user_network_directory=user_network_dir,
            download_enabled=True,
        )

    def read_text(self, image: np.ndarray, allowlist: str = "") -> str:
        """Extract text from an image region. Returns empty string if nothing found.

        Args:
            image: BGR / BGRA crop
            allowlist: if non-empty, restrict OCR output to these characters.
                Useful for digit-only or known-charset reads (e.g. card ranks,
                stack amounts). Empty string = no restriction (general OCR).
        """
        self._init()
        processed = self._preprocess(image)
        try:
            kwargs = {"detail": 0}
            if allowlist:
                kwargs["allowlist"] = allowlist
            results = self._reader.readtext(processed, **kwargs)
        except Exception:
            logger.warning("OCR call failed", exc_info=True)
            return ""
        return " ".join(results).strip()

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        """Upscale, grayscale, threshold to improve OCR accuracy."""
        if image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

        h, w = image.shape[:2]
        # Upscale 2x for small text
        image = cv2.resize(image, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return thresh
