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
        import easyocr
        self._reader = easyocr.Reader(["en"], gpu=self._gpu)

    def read_text(self, image: np.ndarray) -> str:
        """Extract text from an image region. Returns empty string if nothing found."""
        self._init()
        processed = self._preprocess(image)
        try:
            results = self._reader.readtext(processed, detail=0)
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
