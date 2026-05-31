"""Unit tests for Phase 1.5 v3.2 Step 3.2 — OCR-2 wire 进 orchestrator.

Linux-only smoke test — 不实际加载 EasyOCR Reader.
仅验证:
- ATTENTION_MODE=0:self.ocr_focus is None(不分配 VRAM)
- ATTENTION_MODE=1:self.ocr_focus 是 OCREngine instance + name="focus"
- self.ocr 始终存在,name="global"
- self.ocr.default_allowlist 跟 ATTENTION_MODE 联动

实际双 OCR 协作行为在 Sub-step 3.5 真 Win 验.
"""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def reset_config():
    """Reset config singletons before each test."""
    yield
    # No-op cleanup — config singletons are read at import time
    # so tests rely on monkeypatch to inject ATTENTION_MODE


class TestOrchestratorOCRWire:
    def _make_orchestrator(self, monkeypatch, attention_mode: bool):
        """Construct orchestrator with desired ATTENTION_MODE without crash."""
        import config
        monkeypatch.setattr(config, "ATTENTION_MODE", attention_mode)

        # 避免实际加载 ROI / DB / EasyOCR — patch heavy deps
        from pipeline.orchestrator import PipelineOrchestrator

        # Patch heavy collaborators to None / Mock
        with patch.object(PipelineOrchestrator, "_probe_db", return_value=False), \
             patch("pipeline.orchestrator.ROIManager"), \
             patch("pipeline.orchestrator.ScreenCapturer"):
            o = PipelineOrchestrator(roi_profile="party_poker_9", observer_mode=True)
        return o

    def test_attention_mode_off_no_focus_ocr(self, monkeypatch):
        """ATTENTION_MODE=0:ocr_focus is None,ocr.default_allowlist 空."""
        o = self._make_orchestrator(monkeypatch, attention_mode=False)
        assert o.ocr is not None
        assert o.ocr.name == "global"
        assert o.ocr.default_allowlist == ""  # 向后兼容
        assert o.ocr_focus is None  # 不分配 VRAM

    def test_attention_mode_on_focus_ocr_wired(self, monkeypatch):
        """ATTENTION_MODE=1:ocr_focus 是 OCREngine + name='focus'."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        assert o.ocr is not None
        assert o.ocr.name == "global"
        # default_allowlist 收窄到 attention 字符集
        assert "弃牌" in o.ocr.default_allowlist
        assert "跟注" in o.ocr.default_allowlist
        # OCR-2 wired
        assert o.ocr_focus is not None
        assert o.ocr_focus.name == "focus"
        assert o.ocr_focus.default_allowlist == ""  # dynamic per call

    def test_attention_mode_ocr_instances_independent(self, monkeypatch):
        """T96 invariant:双 OCR instance 各自独立."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        assert o.ocr is not o.ocr_focus
        assert o.ocr._reader is None  # lazy init
        assert o.ocr_focus._reader is None
