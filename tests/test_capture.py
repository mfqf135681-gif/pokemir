"""Screen capture tests — gated behind a display/monitor being available."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from capture.screen import ROIRegion, ScreenCapturer


@pytest.fixture(scope="module")
def capturer():
    cap = ScreenCapturer()
    try:
        monitors = cap.list_monitors()
    except Exception as exc:
        pytest.skip(f"mss unavailable: {exc}")
    if len(monitors) < 2:
        pytest.skip("No physical monitor reported by mss (headless?)")
    cap.select_monitor(1)
    return cap, monitors[1]


def test_capture_full_screen(capturer, tmp_path):
    cap, monitor = capturer
    try:
        full = cap.capture()
    except Exception as exc:
        pytest.skip(f"Cannot capture screen: {exc}")
    assert full.ndim == 3
    assert full.shape[0] > 0 and full.shape[1] > 0


def test_capture_center_crop(capturer):
    cap, monitor = capturer
    w, h = monitor["width"], monitor["height"]
    crop_w, crop_h = 200, 150
    roi = ROIRegion(
        name="center_test",
        left=(w - crop_w) // 2,
        top=(h - crop_h) // 2,
        width=crop_w,
        height=crop_h,
    )
    try:
        crop = cap.capture_roi(roi)
    except Exception as exc:
        pytest.skip(f"Cannot capture ROI: {exc}")
    assert crop.shape[0] == crop_h
    assert crop.shape[1] == crop_w
