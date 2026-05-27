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

    # iscard probability threshold below which prediction is suppressed (#6).
    # Tunable: raise to be more aggressive at rejecting non-card pixels.
    ISCARD_GATE = 0.5

    def __init__(self):
        self._model = None
        self._device = None
        self._ranks = None
        self._suits = None
        self._input_h = None
        self._input_w = None
        self._transform = None
        self._available: Optional[bool] = None
        # Temperature scaling (#5). Old ckpts default to 1.0 (no rescaling).
        self._T_rank: float = 1.0
        self._T_suit: float = 1.0
        self._T_iscard: float = 1.0
        # Whether checkpoint contains the iscard head (#6).  Set at load.
        self._has_iscard: bool = False

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
            # Backward-compat: old ckpts have no iscard_head weights. strict=False
            # lets the freshly-initialized head pass through (predicts random until
            # retrained with showdown_noncard data — disabled by _has_iscard guard).
            missing, _unexpected = model.load_state_dict(ckpt["state_dict"], strict=False)
            self._has_iscard = not any("iscard_head" in m for m in missing)
            model.eval()
            self._model = model

            # Load temperature scaling (#5) — old ckpts → T=1.0 (no rescaling).
            T = ckpt.get("temperature") or {}
            self._T_rank = float(T.get("rank", 1.0))
            self._T_suit = float(T.get("suit", 1.0))
            self._T_iscard = float(T.get("iscard", 1.0))

            self._transform = transforms.Compose([
                transforms.Resize((self._input_h, self._input_w)),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
            logger.info(f"CNN classifier ready on {self._device} "
                        f"(val_both_acc={ckpt.get('val_both_acc', 'n/a')} "
                        f"sd_acc={ckpt.get('val_showdown_both_acc', 'n/a')} "
                        f"T=[r={self._T_rank:.2f},s={self._T_suit:.2f},i={self._T_iscard:.2f}] "
                        f"iscard_head={'on' if self._has_iscard else 'off'})")
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
            heads = self._model(x)
            # Backward-compat: old 2-head CardCNN returned (pr, ps);
            # new 3-head returns (pr, ps, pic).
            if len(heads) == 3:
                pr, ps, pic = heads
            else:
                pr, ps = heads
                pic = None
            # Temperature-scaled softmax (#5) → calibrated probability.
            rank_softmax = torch.softmax(pr / self._T_rank, dim=1)
            suit_softmax = torch.softmax(ps / self._T_suit, dim=1)
            rank_idx = int(pr.argmax(1).item())
            suit_idx = int(ps.argmax(1).item())
            rank_conf = float(rank_softmax[0, rank_idx].item())
            suit_conf = float(suit_softmax[0, suit_idx].item())
            # iscard gate (#6) — only when the head was trained (noncard data present)
            iscard_conf = None
            if self._has_iscard and pic is not None:
                ic_softmax = torch.softmax(pic / self._T_iscard, dim=1)
                iscard_conf = float(ic_softmax[0, 1].item())  # P(CARD)
                if iscard_conf < self.ISCARD_GATE:
                    logger.debug(f"CNN suppressed: iscard_conf={iscard_conf:.3f} < {self.ISCARD_GATE}")
                    return None
        out = {
            "rank": self._ranks[rank_idx], "suit": self._suits[suit_idx],
            "rank_conf": rank_conf, "suit_conf": suit_conf,
        }
        if iscard_conf is not None:
            out["iscard_conf"] = iscard_conf
        return out
