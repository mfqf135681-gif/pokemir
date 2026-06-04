"""tests/test_roi_geom.py — roi_geom.map_zoomed_roi 纯函数单测(无 cv2,Linux 可跑)。

验证两阶段放大框选的坐标回映正确——这是 roi_config A2 唯一"算错就全偏"的核;
GUI 部分(cv2)本机无法验,只此可离线证。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from roi_geom import map_zoomed_roi  # noqa: E402


def test_roundtrip_exact():
    # 原图目标 (110,105,20,15);粗框 (100,100,40,30);pad=24,scale=8
    # crop 起点 = (76,76);目标在 crop 内 = (34,29,20,15) → ×8 = (272,232,160,120)
    assert map_zoomed_roi((100, 100, 40, 30), (272, 232, 160, 120), 8, 24, 1920, 1080) \
        == (110, 105, 20, 15)


def test_clamp_left_top_edge():
    # 粗框贴左上:cx-pad = -14 → crop 起点 clamp 到 0
    # 目标原图 (12,8,20,15) → crop 内 (12,8) ×8 = (96,64,160,120)
    assert map_zoomed_roi((10, 10, 40, 30), (96, 64, 160, 120), 8, 24, 1920, 1080) \
        == (12, 8, 20, 15)


def test_clamp_size_within_image():
    # 贴右下 + 精框过大 → w/h 收到图内,且 ≥1
    x, y, w, h = map_zoomed_roi((1900, 1070, 10, 5), (0, 0, 800, 800), 8, 24, 1920, 1080)
    assert 0 <= x < 1920 and 0 <= y < 1080
    assert w >= 1 and h >= 1 and x + w <= 1920 and y + h <= 1080


def test_skip_fine_uses_coarse_shape():
    # 精框从原点起、与粗框同放大尺寸 → 回映应≈粗框(同位置同大小)
    # 粗框(200,150,40,30),pad24→crop起点(176,126);精框(192,192,320,240)
    # = ((200-176)*8,(150-126)*8, 40*8,30*8) → 回映 (200,150,40,30)
    assert map_zoomed_roi((200, 150, 40, 30), (192, 192, 320, 240), 8, 24, 1920, 1080) \
        == (200, 150, 40, 30)


if __name__ == "__main__":
    test_roundtrip_exact()
    test_clamp_left_top_edge()
    test_clamp_size_within_image()
    test_skip_fine_uses_coarse_shape()
    print("✅ roi_geom.map_zoomed_roi 4/4 通过")
