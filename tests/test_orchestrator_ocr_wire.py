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

    def test_capture_focus_seat_ocr_uses_real_SeatROI_attrs(self, monkeypatch):
        """T102 regression:验证 _capture_focus_seat_ocr 用真实 SeatROI 属性名.

        Bug 实测发现:之前用 seat.amount(不存在),Win 端 mode=1 every tick crash.
        Linux MagicMock 没抓到(mock auto-creates any attr).
        本测用真实 SeatROI dataclass(amount_area=None)验证 no AttributeError.
        """
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        from capture.roi import SeatROI, ROIRegion
        from unittest.mock import MagicMock
        # 真 SeatROI dataclass, amount_area=None 触发 optional path
        # ROIRegion(name, left, top, width, height)
        seat3 = SeatROI(
            seat_index=3,
            action_area=ROIRegion("action", 0, 0, 0, 0),  # width=0 触发 skip
            amount_area=None,  # T102 关键:Optional None
            stack_area=ROIRegion("stack", 0, 0, 0, 0),
        )
        rois = MagicMock()
        rois.seat_regions = [seat3]
        # 该调用之前会抛 AttributeError 'amount';修复后正常返回
        result = o._capture_focus_seat_ocr(rois, focus_seat=3)
        assert result == {"action_text": "", "amount_text": "", "chip_text": ""}


class TestAttentionFocusResultsT99:
    """Step 3.3b — _tick attention 分支 + _attention_focus_results storage."""

    def _make_orchestrator(self, monkeypatch, attention_mode: bool):
        import config
        monkeypatch.setattr(config, "ATTENTION_MODE", attention_mode)
        from pipeline.orchestrator import PipelineOrchestrator
        with patch.object(PipelineOrchestrator, "_probe_db", return_value=False), \
             patch("pipeline.orchestrator.ROIManager"), \
             patch("pipeline.orchestrator.ScreenCapturer"):
            return PipelineOrchestrator(roi_profile="party_poker_9", observer_mode=True)

    def test_attention_focus_results_initialized_mode_off(self, monkeypatch):
        """mode=0 时 _attention_focus_results 仍 init 为空 dict."""
        o = self._make_orchestrator(monkeypatch, attention_mode=False)
        assert hasattr(o, "_attention_focus_results")
        assert o._attention_focus_results == {}

    def test_attention_focus_results_initialized_mode_on(self, monkeypatch):
        """mode=1 时 _attention_focus_results init 为空 dict(tick 内填充)."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        assert hasattr(o, "_attention_focus_results")
        assert o._attention_focus_results == {}

    def test_attention_focus_results_writeable(self, monkeypatch):
        """_capture_focus_seat_ocr 返值能 assign 到 _attention_focus_results."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        # 模拟 tick 内逻辑
        o._attention_focus_results = {"action_text": "跟注", "amount_text": "100"}
        assert o._attention_focus_results["action_text"] == "跟注"
        assert o._attention_focus_results["amount_text"] == "100"


class TestPatternDMergeT100:
    """Step 3.3c — _pattern_d_merge_action / _pattern_d_merge_amount."""

    def _make_orchestrator(self, monkeypatch, attention_mode: bool):
        import config
        monkeypatch.setattr(config, "ATTENTION_MODE", attention_mode)
        from pipeline.orchestrator import PipelineOrchestrator
        with patch.object(PipelineOrchestrator, "_probe_db", return_value=False), \
             patch("pipeline.orchestrator.ROIManager"), \
             patch("pipeline.orchestrator.ScreenCapturer"):
            return PipelineOrchestrator(roi_profile="party_poker_9", observer_mode=True)

    # ─── action merge ────────────────────────────────────────────────

    def test_merge_action_mode_off_returns_original(self, monkeypatch):
        o = self._make_orchestrator(monkeypatch, attention_mode=False)
        assert o._pattern_d_merge_action(3, "") == ""
        assert o._pattern_d_merge_action(3, "跟注") == "跟注"

    def test_merge_action_legacy_already_captured(self, monkeypatch):
        """legacy 已抓到 → 不覆盖."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        o.tracker.start_new_hand("t")
        o.tracker._pointer_state["current_seat"] = 3
        o._attention_focus_results = {"action_text": "弃牌"}  # OCR-2 也有
        # legacy 给 "跟注" 不变
        assert o._pattern_d_merge_action(3, "跟注") == "跟注"

    def test_merge_action_not_focus_seat(self, monkeypatch):
        """非 focus seat → 不 fallback."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        o.tracker.start_new_hand("t")
        o.tracker._pointer_state["current_seat"] = 3
        o._attention_focus_results = {"action_text": "弃牌"}
        # seat 5 不是 focus → 不 fallback
        assert o._pattern_d_merge_action(5, "") == ""

    def test_merge_action_no_ocr2_data(self, monkeypatch):
        """OCR-2 无数据 → 不 fallback."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        o.tracker.start_new_hand("t")
        o.tracker._pointer_state["current_seat"] = 3
        o._attention_focus_results = {}  # 空
        assert o._pattern_d_merge_action(3, "") == ""

    def test_merge_action_fallback_fires(self, monkeypatch):
        """ATTENTION + focus + legacy 空 + OCR-2 有 → fallback."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        o.tracker.start_new_hand("t")
        o.tracker._pointer_state["current_seat"] = 3
        o._attention_focus_results = {"action_text": "弃牌"}
        result = o._pattern_d_merge_action(3, "")
        assert result == "弃牌"

    # ─── amount merge ────────────────────────────────────────────────

    def test_merge_amount_fallback_fires(self, monkeypatch):
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        o.tracker.start_new_hand("t")
        o.tracker._pointer_state["current_seat"] = 3
        o._attention_focus_results = {"amount_text": "100"}
        assert o._pattern_d_merge_amount(3, "") == "100"

    def test_merge_amount_mode_off(self, monkeypatch):
        o = self._make_orchestrator(monkeypatch, attention_mode=False)
        # mode=0 永不 fallback
        o.tracker.start_new_hand("t")
        o.tracker._pointer_state["current_seat"] = 3
        o._attention_focus_results = {"amount_text": "100"}
        assert o._pattern_d_merge_amount(3, "") == ""


class TestMultiPotObserveT101:
    """Step 3.4 — _observe_multi_pot framework stub."""

    def _make_orchestrator(self, monkeypatch, attention_mode: bool):
        import config
        monkeypatch.setattr(config, "ATTENTION_MODE", attention_mode)
        from pipeline.orchestrator import PipelineOrchestrator
        with patch.object(PipelineOrchestrator, "_probe_db", return_value=False), \
             patch("pipeline.orchestrator.ROIManager"), \
             patch("pipeline.orchestrator.ScreenCapturer"):
            return PipelineOrchestrator(roi_profile="party_poker_9", observer_mode=True)

    def test_observe_mode_off_returns_empty(self, monkeypatch):
        o = self._make_orchestrator(monkeypatch, attention_mode=False)
        from unittest.mock import MagicMock
        rois = MagicMock()
        result = o._observe_multi_pot(rois)
        assert result == {"main_pot": None, "side_pot_count": 0}

    def test_observe_mode_on_rois_none(self, monkeypatch):
        """rois None → 不 crash."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        result = o._observe_multi_pot(None)
        assert result == {"main_pot": None, "side_pot_count": 0}

    def test_observe_mode_on_no_pot_size_attr(self, monkeypatch):
        """rois 没 pot_size 属性 → 防御性返回."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        from types import SimpleNamespace
        rois = SimpleNamespace(pot_size=None)
        result = o._observe_multi_pot(rois)
        assert result == {"main_pot": None, "side_pot_count": 0}

    def test_observe_side_pot_count_zero_for_now(self, monkeypatch):
        """T101 framework stub:side_pot_count 永远 0(待 Win UI verify 后扩展)."""
        o = self._make_orchestrator(monkeypatch, attention_mode=True)
        result = o._observe_multi_pot(None)
        assert result["side_pot_count"] == 0
