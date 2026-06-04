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


# ── ROI 参数化派生原语(无 cv2,Linux 可单测)─────────────────────────
# 用途:把每座 ROI 表达成"相对该座 card_marker 锚的偏移",从一个参考座抽模板、
# 套到同组其他座 → 消手框抖动 + 立可迁移几何模型(见 roi_derive.py)。

def roi_offset(anchor, box):
    """box 相对 anchor(card_marker)的偏移 (dx, dy, w, h)。
    dx/dy = 左上角差;w/h = box 自身尺寸(锚只定位、不定尺寸)。"""
    return (box[0] - anchor[0], box[1] - anchor[1], box[2], box[3])


def apply_offset(anchor, offset):
    """anchor + offset → box [l, t, w, h]。roi_offset 的逆运算。"""
    return [anchor[0] + offset[0], anchor[1] + offset[1], offset[2], offset[3]]


def mirror_box_x(box, axis):
    """绕垂直轴 x=axis 镜像 box → [l, t, w, h](宽高不变,左右翻、上下不动)。
    新 left = 2*axis - (left + w)(右边缘变左边缘)。用于左右座互推。"""
    return [int(round(2 * axis - (box[0] + box[2]))), box[1], box[2], box[3]]
