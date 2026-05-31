"""Unit tests for Phase 1.5 v3.2 Step 3.1 — OCREngine 多 instance 重构.

Linux-only smoke test — 不实际加载 EasyOCR Reader(无需 GPU/模型文件).
仅验证:
- name / default_allowlist 字段
- 多 instance 互不干扰
- 向后兼容(old API 仍 work)

Real OCR 跑测在 Win 端 Step 3.5 真 verify.
"""

import pytest

from recognition.ocr import OCREngine


class TestOCREngineMultiInstance:
    def test_default_construction(self):
        """向后兼容:OCREngine(gpu=False) 仍能 construct."""
        e = OCREngine(gpu=False)
        assert e.name == "default"
        assert e.default_allowlist == ""

    def test_named_construction(self):
        """T96: 命名 instance 用于 OCR-1 全局 / OCR-2 专注 区分."""
        e1 = OCREngine(gpu=False, name="global")
        e2 = OCREngine(gpu=False, name="focus")
        assert e1.name == "global"
        assert e2.name == "focus"
        assert e1 is not e2  # 独立 instance

    def test_default_allowlist(self):
        """T96: instance-level default_allowlist 不破坏 read_text 签名."""
        e = OCREngine(
            gpu=False,
            name="global",
            default_allowlist="弃牌跟注让牌加下0123456789",
        )
        assert e.default_allowlist == "弃牌跟注让牌加下0123456789"

    def test_multi_instance_independence(self):
        """两 instance 各自 hold state,不共享 reader."""
        e1 = OCREngine(gpu=False, name="ocr1", default_allowlist="弃牌")
        e2 = OCREngine(gpu=False, name="ocr2", default_allowlist="跟注让加")

        # 各自 reader 都 None(lazy init)
        assert e1._reader is None
        assert e2._reader is None

        # 各自 default_allowlist 独立
        assert e1.default_allowlist != e2.default_allowlist

        # 字段不共享(改 e1 不影响 e2)
        e1._reader = "mock-reader-1"
        assert e2._reader is None

    def test_gpu_param_isolated(self):
        """GPU 设置 instance-level."""
        e_cpu = OCREngine(gpu=False, name="cpu_ocr")
        e_gpu = OCREngine(gpu=True, name="gpu_ocr")
        assert e_cpu._gpu is False
        assert e_gpu._gpu is True

    def test_pattern_d_naming_convention(self):
        """Pattern D 命名:OCR-1 全局 + OCR-2 专注."""
        ocr1 = OCREngine(
            gpu=True,
            name="global",
            default_allowlist="弃牌跟注让牌加下0123456789",
        )
        ocr2 = OCREngine(
            gpu=True,
            name="focus",
            default_allowlist="",  # dynamic per call
        )
        assert ocr1.name == "global"
        assert ocr2.name == "focus"
        assert ocr1.default_allowlist != ocr2.default_allowlist
