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


class TestPatternDFocusSeatT98:
    """Step 3.3a — focus seat helper + OCR-2 capture skeleton."""

    def _make_orchestrator(self, monkeypatch, attention_mode: bool):
        import config
        monkeypatch.setattr(config, "ATTENTION_MODE", attention_mode)
        from pipeline.orchestrator import PipelineOrchestrator
        with patch.object(PipelineOrchestrator, "_probe_db", return_value=False), \
             patch("pipeline.orchestrator.ROIManager"), \
             patch("pipeline.orchestrator.ScreenCapturer"):
            return PipelineOrchestrator(roi_profile="party_poker_9", observer_mode=True)

    def test_get_focus_seat_no_active_hand(self, monkeypatch):
        """没 active hand → 返 None."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        assert o.tracker.current_hand is None
        assert o.get_focus_seat() is None

    def test_get_focus_seat_reads_pointer_state(self, monkeypatch):
        """active hand + _pointer_state["current_seat"]=3 → 返 3."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        o.tracker.start_new_hand("test")
        o.tracker._pointer_state["current_seat"] = 3
        assert o.get_focus_seat() == 3

    def test_capture_focus_seat_ocr_attention_off(self, monkeypatch):
        """ATTENTION_MODE=0 → 返空 dict(skip)."""
        o = self._make_orchestrator(monkeypatch, attention_mode=False)
        from unittest.mock import MagicMock
        rois = MagicMock()
        assert o._capture_focus_seat_ocr(rois, focus_seat=3) == {}

    def test_capture_focus_seat_ocr_no_focus_seat(self, monkeypatch):
        """focus_seat None → 返空 dict."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        from unittest.mock import MagicMock
        rois = MagicMock()
        assert o._capture_focus_seat_ocr(rois, focus_seat=None) == {}

    def test_capture_focus_seat_ocr_no_ocr_focus(self, monkeypatch):
        """ocr_focus None → 返空 dict(防御性)."""
        o = self._make_orchestrator(monkeypatch, attention_mode=False)
        # mode=0 时 ocr_focus 本来就是 None,跟 attention_mode 切换无关
        # 这里 force 显式状态
        o.ocr_focus = None
        from unittest.mock import MagicMock
        rois = MagicMock()
        assert o._capture_focus_seat_ocr(rois, focus_seat=3) == {}
