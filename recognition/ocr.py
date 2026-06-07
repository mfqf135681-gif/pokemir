"""OCR engine wrapper for reading text from poker table ROIs."""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class OCREngine:
    """Wraps EasyOCR for reading action text, amounts, and card ranks.

    Pre-processes images with upscale + thresholding to improve
    accuracy on small / anti-aliased fonts.

    Phase 1.5 v3.2 Step 3.1 (T96, 2026-05-31): instance-specialized 改造.
    支持多 instance — 每个 instance 可以有自己的:
    - name(诊断用,标识 OCR-1 全局 / OCR-2 专注)
    - default_allowlist(instance-level allowlist,read_text 仍可覆盖)
    每个 instance 各持一个 EasyOCR Reader(VRAM ~1.5GB × N).
    """

    def __init__(
        self,
        gpu: bool = False,
        *,
        name: str = "default",
        default_allowlist: str = "",
    ):
        self._reader = None
        self._gpu = gpu
        self._name = name
        self._default_allowlist = default_allowlist

    @property
    def name(self) -> str:
        return self._name

    @property
    def default_allowlist(self) -> str:
        return self._default_allowlist

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
        logger.info(f"OCREngine[{self._name}] init: gpu={self._gpu}")
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
                若空 → 使用 instance.default_allowlist(T96 instance-specialized)
            ensemble: #8 if True, run OCR on TWO preprocessed variants (default
                2x upscaled + 3x upscaled) and pick the longer non-empty result.
                Use sparingly (2x cost); good for action / id where accuracy matters.
        """
        self._init()
        if not allowlist:
            allowlist = self._default_allowlist
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

    def read_text_batch(self, images: list, allowlist: str = "", scale: int = 3) -> list:
        """T73(2026-05-29):batch OCR — 多张图一次 GPU call.

        Args:
            images: list of np.ndarray (BGR/BGRA) — 不同尺寸允许(用 n_width/n_height auto-resize)
            allowlist: 全部图共用一个 allowlist.若空 → 用 instance.default_allowlist
                       (T96 instance-specialized).
            scale: 内部 mag_ratio,默认 3(高准度 + 仍快)

        Returns:
            list[str] — 跟输入 1-1 对应,失败位置返 "".

        实施:
        - 不做自己 CPU preprocess(交给 EasyOCR 内部 mag_ratio)
        - 仅 BGRA → BGR 修正(EasyOCR 不支持 alpha)
        - None 或空图返回 "" 占位
        """
        self._init()
        if not allowlist:
            allowlist = self._default_allowlist
        if not images:
            return []
        # Step 1: BGRA fix + 收集有效图
        valid_imgs = []
        valid_idx = []
        for i, img in enumerate(images):
            if img is None or img.size == 0:
                continue
            if img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            valid_imgs.append(img)
            valid_idx.append(i)
        if not valid_imgs:
            return [""] * len(images)
        # Step 2: n_width / n_height 用最大尺寸 × scale(EasyOCR 自动 resize)
        max_h = max(img.shape[0] for img in valid_imgs)
        max_w = max(img.shape[1] for img in valid_imgs)
        n_width = max(max_w * scale, 64)
        n_height = max(max_h * scale, 32)
        # Step 3: 批量 OCR
        try:
            kwargs = {"detail": 0, "n_width": n_width, "n_height": n_height}
            if allowlist:
                kwargs["allowlist"] = allowlist
            results = self._reader.readtext_batched(valid_imgs, **kwargs)
        except Exception:
            logger.warning("OCR batch call failed", exc_info=True)
            return [""] * len(images)
        # Step 4: 结果回填(readtext_batched 返回 list of list)
        out = [""] * len(images)
        for pos, result in zip(valid_idx, results):
            if isinstance(result, list):
                out[pos] = " ".join(str(r) for r in result).strip()
            else:
                out[pos] = str(result).strip()
        return out

    def recognize_boxes(self, frame_grey: np.ndarray, boxes: list,
                        allowlist: str = "", batch_size: int = 16) -> list:
        """#237(2026-06-07):recognize-only — 跳过 CRAFT 检测，对整帧灰度图的固定框直接识别。

        EasyOCR `reader.recognize(grey, horizontal_list, free_list, detail=0)`：把检测与识别
        解耦，免掉最贵的检测阶段（控制 bench ×3.1 vs 逐 ROI readtext）。

        Args:
            frame_grey: 整帧【灰度】图（H×W，调用方负责 BGRA/BGR→GRAY）
            boxes: list of [x_min, x_max, y_min, y_max]（帧内坐标，与 horizontal_list 同格式）
            allowlist: 全部框共用一个 allowlist（recognize 限制同 readtext）。空 → instance 默认
            batch_size: 识别器 batch（recognize 内部对多框批识别）

        Returns:
            list[str] — 与 boxes 1-1（detail=0 → recognize 按 horizontal_list 顺序返回文本，
            context7 /jaidedai/easyocr 确认）。失败 / 不齐 → 以 "" 补位占满 len(boxes)。
        """
        self._init()
        if not allowlist:
            allowlist = self._default_allowlist
        if not boxes:
            return []
        try:
            kwargs = {"detail": 0, "free_list": [], "batch_size": batch_size}
            if allowlist:
                kwargs["allowlist"] = allowlist
            results = self._reader.recognize(frame_grey, horizontal_list=boxes, **kwargs)
        except Exception:
            logger.warning("OCR recognize-only call failed", exc_info=True)
            return [""] * len(boxes)
        out = [str(r).strip() if r is not None else "" for r in (results or [])]
        if len(out) < len(boxes):
            out += [""] * (len(boxes) - len(out))
        return out[:len(boxes)]

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
