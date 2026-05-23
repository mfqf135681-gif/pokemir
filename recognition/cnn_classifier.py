"""Custom-trained CNN classifier for poker card recognition.

Loads a model trained by tools/train_card_cnn.py and runs inference on
cropped card images. Falls back gracefully if the model file or torch
is unavailable — CardRecognizer downstream chains to vision / heuristic.

Inference target: <50ms per card on RTX 5070 Ti (FP32);
~50-200ms on modest CPU.

Red line compliance: pure local inference; no network, no DOM, no input
injection. Reads only the local .pth and the image passed in.
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Resolve model path relative to project root, matching train_card_cnn.py
_MODEL_PATH = Path(__file__).parent.parent / "models" / "card_cnn.pth"


class CnnClassifier:
    """Loads tools/train_card_cnn.py's output and exposes a recognize() API."""

    def __init__(self):
        self._model = None
        self._device = None
        self._ranks = None
        self._suits = None
        self._input_h = None
        self._input_w = None
        self._transform = None
        self._available: Optional[bool] = None

    @property
    def available(self) -> bool:
        if self._available is None:
            self._available = self._try_init()
        return self._available

    def _try_init(self) -> bool:
        if not _MODEL_PATH.exists():
            logger.debug(f"CNN model not found at {_MODEL_PATH}; CNN path disabled")
            return False
        try:
            import torch
            from torchvision import transforms

            # Architecture must match training; import locally to avoid hard dep on torch
            # for envs where CNN isn't used.
            from tools.train_card_cnn import CardCNN

            self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            ckpt = torch.load(_MODEL_PATH, map_location=self._device, weights_only=False)
            self._ranks = ckpt["ranks"]
            self._suits = ckpt["suits"]
            self._input_h = ckpt["input_h"]
            self._input_w = ckpt["input_w"]

            model = CardCNN().to(self._device)
            model.load_state_dict(ckpt["state_dict"])
            model.eval()
            self._model = model

            self._transform = transforms.Compose([
                transforms.Resize((self._input_h, self._input_w)),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
            logger.info(f"CNN classifier ready on {self._device} "
                        f"(val_both_acc={ckpt.get('val_both_acc', 'n/a')})")
            return True
        except ImportError as exc:
            logger.info(f"CNN path disabled (missing dep: {exc})")
            return False
        except Exception:
            logger.warning("CNN init failed", exc_info=True)
            return False

    def identify_card(self, image: np.ndarray) -> Optional[dict]:
        """Return {"rank": "A", "suit": "h"} or None on failure."""
        if not self.available or self._model is None:
            return None
        try:
            return self._predict(image)
        except Exception:
            logger.debug("CNN inference failed", exc_info=True)
            return None

    def _predict(self, image: np.ndarray) -> Optional[dict]:
        import torch
        from PIL import Image as PILImage

        if image is None or image.size == 0:
            return None
        # mss returns BGRA; convert to RGB for PIL
        if image.ndim == 3 and image.shape[2] == 4:
            image = image[..., :3][..., ::-1]  # BGRA -> RGB
        elif image.ndim == 3 and image.shape[2] == 3:
            image = image[..., ::-1]  # BGR -> RGB
        pil = PILImage.fromarray(image).convert("RGB")

        x = self._transform(pil).unsqueeze(0).to(self._device)
        with torch.no_grad():
            pr, ps = self._model(x)
            rank_idx = int(pr.argmax(1).item())
            suit_idx = int(ps.argmax(1).item())
        return {"rank": self._ranks[rank_idx], "suit": self._suits[suit_idx]}
