"""tools/roi_geom.py — ROI 几何纯函数(无 cv2 / 无重依赖)。

从 roi_config 抽出"算错就全偏"的坐标回映,隔离成纯函数 → 可在**无 cv2 的
Linux** 上单测(roi_config 因 `import cv2` 在本机无法 import)。GUI 部分留在
roi_config,只能 Win 端试飞;本模块是唯一可离线验证的核。
"""


def map_zoomed_roi(coarse, fine, scale, pad, img_w, img_h):
    """两阶段放大框选的坐标回映:放大图上的精框 → 原图坐标。

    coarse = (cx, cy, cw, ch)  阶段1 粗框(原图坐标)
    fine   = (fx, fy, fw, fh)  阶段2 在【放大图】上框的精框
    scale  放大倍数;pad 粗框四周裁剪留白(像素,原图尺度)
    img_w / img_h  原图尺寸(用于 clamp 不越界)

    放大图 = img[cy-pad : …, cx-pad : …] 放大 scale 倍(裁剪起点 clamp 到 0)。
    故原图坐标 = 裁剪起点 + 精框坐标 / scale。返回 (x, y, w, h)。
    """
    cx, cy = coarse[0], coarse[1]
    crop_x0 = max(0, cx - pad)
    crop_y0 = max(0, cy - pad)
    fx, fy, fw, fh = fine
    x = crop_x0 + int(round(fx / scale))
    y = crop_y0 + int(round(fy / scale))
    w = int(round(fw / scale))
    h = int(round(fh / scale))
    # clamp 到原图内(防越界 / 负值)
    x = max(0, min(x, img_w - 1))
    y = max(0, min(y, img_h - 1))
    w = max(1, min(w, img_w - x))
    h = max(1, min(h, img_h - y))
    return (x, y, w, h)
