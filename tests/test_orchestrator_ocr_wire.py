"""Unit tests — stack_area OCR 的 all-in 胜率"%" 检测(R15)。

(2026-06-06 P3:本文件原大半测 ATTENTION_MODE 双OCR/focus/pattern_d/multi_pot 实验,
 该实验整套退役删除 → 仅保留仍 live 的 _ocr_stack_chips 测试。)

Linux-only smoke test — 不实际加载 EasyOCR Reader。
"""

from unittest.mock import patch, MagicMock

import pytest


class TestStackPercentDetectT103:
    """R15 — stack_area 全押后显胜率"%" 检测,应返 None 不当筹码。"""

    def _make_orchestrator(self):
        from pipeline.orchestrator import PipelineOrchestrator
        with patch.object(PipelineOrchestrator, "_probe_db", return_value=False), \
             patch("pipeline.orchestrator.ROIManager"), \
             patch("pipeline.orchestrator.ScreenCapturer"):
            return PipelineOrchestrator(roi_profile="party_poker_9", observer_mode=True)

    def test_ocr_stack_chips_normal_returns_amount(self):
        """正常 stack OCR "1500" → 返 1500.0."""
        o = self._make_orchestrator()
        o.ocr = MagicMock()
        o.ocr.read_text = MagicMock(return_value="1500")
        assert o._ocr_stack_chips(img=MagicMock(), seat_idx=3) == 1500.0

    def test_ocr_stack_chips_percent_returns_none(self):
        """all-in equity "78%" → 返 None(关键 R15 fix)."""
        o = self._make_orchestrator()
        o.ocr = MagicMock()
        o.ocr.read_text = MagicMock(return_value="78%")
        assert o._ocr_stack_chips(img=MagicMock(), seat_idx=3) is None

    def test_ocr_stack_chips_percent_with_leading_text(self):
        """"100%" 也应返 None(防御性)."""
        o = self._make_orchestrator()
        o.ocr = MagicMock()
        o.ocr.read_text = MagicMock(return_value="100%")
        assert o._ocr_stack_chips(img=MagicMock(), seat_idx=3) is None

    def test_ocr_stack_chips_empty_returns_none(self):
        """空 OCR → None(原行为不变)."""
        o = self._make_orchestrator()
        o.ocr = MagicMock()
        o.ocr.read_text = MagicMock(return_value="")
        assert o._ocr_stack_chips(img=MagicMock(), seat_idx=3) is None

    def test_ocr_stack_chips_allowlist_includes_percent(self):
        """allowlist 包含 % 才能 detect all-in equity."""
        o = self._make_orchestrator()
        o.ocr = MagicMock()
        o.ocr.read_text = MagicMock(return_value="50")
        o._ocr_stack_chips(img=MagicMock(), seat_idx=3)
        call_args = o.ocr.read_text.call_args
        assert "%" in call_args.kwargs.get("allowlist", ""), \
            "stack OCR allowlist must include '%' to detect all-in equity"
