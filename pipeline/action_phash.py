"""pipeline/action_phash.py — #240 text-shape phash 动作识别器(色盲+位置无关二元桩)。

每座 action_area → 饱和度抠白字 → 文字外接框裁出 → resize 归一 → aHash → 比各动作参考
(过牌/加注/跟注/下注),hamming≤阈 → 该中文词(喂 recognition.actions.ActionRecognizer.parse);
落空 → None(无动作/idle)。色盲(治下注实测多色)+ 平移/尺度无关(治各座 ROI 相对位置不一)。

2026-06-12 首字框改革:ROI 改为【物理上只框第一个字】(跟/加/下/过 互异,且把 s4 横幅
排除在框外——s4 曾因横幅白像素拉歪外接框致 bet/call/raise 0/15+ 全盲)。原 first_char
算法切字(找两字列墨最小处取左半)随之删除:框保证单字后它是死代码,且对污染敏感。
⚠️ 参考文件必须用新框重建(旧 refs 是切字时代的 hash,与新框形状不匹配,load 时报警)。

⚠️ `text_shape_hash` 是 cv2 部分,**Win-only**(本 Linux 机无 cv2 时不可验)。
`hamming` / `match_hash` 纯逻辑,Linux 可测。tools/probe_action_phash **import 本模块的
text_shape_hash**(单一实现,防 harness/live 漂移 [[harness-vs-live-codepath-divergence]])。
验收依据见 requirement-discussions/2026-06-07_action-recognition-text-shape-phash.md。
"""
import json
import logging

logger = logging.getLogger(__name__)

# 锚标签 → ActionRecognizer.parse 识别的中文词(过牌/让牌/看牌 都判 CHECK)
LABEL_TO_WORD = {"check": "过牌", "raise": "加注", "call": "跟注", "bet": "下注", "fold": "弃牌"}


def text_norm_img(crop, sat_th=60, val_th=100):
    """抠白字(S<sat_th 且 V>val_th,排除高饱和底色)→ 文字外接框裁出 → resize 16×16 二值图。
    去底色(治下注多色)+ 去位置/尺度(治各座 ROI 相对位置不一)。无字 → None。
    框内应为单个汉字(首字框),输出方形。BGR(imread)与 BGRA(mss 截屏)都吃。Win-only(cv2)。"""
    import cv2
    import numpy as np
    if crop is None or getattr(crop, "size", 0) == 0 or crop.ndim != 3:
        return None
    bgr = cv2.cvtColor(crop, cv2.COLOR_BGRA2BGR) if crop.shape[2] == 4 else crop
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 1] < sat_th) & (hsv[..., 2] > val_th)).astype(np.uint8)
    ys, xs = np.where(mask)
    if len(xs) < 4:
        return None
    text = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return cv2.resize(text * 255, (16, 16), interpolation=cv2.INTER_AREA)


def text_shape_hash(crop, sat_th=60, val_th=100, grid=8):
    """归一化文字形状 grid×grid aHash → grid²-char "0/1" 串;无字 → ""。build/live 须同参数。"""
    import cv2
    norm = text_norm_img(crop, sat_th, val_th)
    if norm is None:
        return ""
    thumb = cv2.resize(norm, (grid, grid), interpolation=cv2.INTER_AREA)
    bits = (thumb > thumb.mean()).astype(int).flatten()
    return "".join(str(b) for b in bits)


def hamming(a, b):
    """64位"0/1"串汉明距离;空/不等长 → 999(本地定义,避免 import orchestrator 成环)。"""
    if not a or not b or len(a) != len(b):
        return 999
    return sum(c1 != c2 for c1, c2 in zip(a, b))


def match_hash(qhash, refs, threshold, margin=0):
    """qhash vs refs({word:[hash,...]}) → (word, dist) 或 None。
    取 min-hamming 的 word(需 ≤threshold);若 margin>0 且次优在 margin 内 → 歧义 → None
    (如 加注/下注 实战粘连时退游戏状态分)。"""
    if not qhash:
        return None
    scored = sorted((min((hamming(qhash, h) for h in hs), default=999), w)
                    for w, hs in refs.items() if hs)
    if not scored or scored[0][0] > threshold:
        return None
    if margin > 0 and len(scored) > 1 and (scored[1][0] - scored[0][0]) < margin:
        return None
    return scored[0][1], scored[0][0]


class ActionPhashReader:
    """live 动作识别器:载参考文件,crop → 中文动作词或 None。"""

    def __init__(self, refs, sat_th=60, val_th=100, threshold=10, margin=0, grid=8):
        self.refs = refs
        self.sat_th, self.val_th = sat_th, val_th
        self.threshold, self.margin, self.grid = threshold, margin, grid

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("first_char"):
            # 旧版参考(切字时代,2026-06-12 前):hash 形状与"首字框+无切字"流水线不匹配,
            # 载入只会全程 mismatch(动作全盲)。响亮报警,逼重建,不静默退化。
            logger.warning("action refs %s 是 first_char 切字时代的旧参考,与首字框流水线不匹配 "
                           "→ 动作识别将全程落空!请用新框重录锚帧并重建(tools/build_action_refs.py)", path)
        return cls(d["refs"], d.get("sat_th", 60), d.get("val_th", 100),
                   int(d.get("match_threshold", 10)), int(d.get("margin", 0)),
                   int(d.get("grid", 8)))

    def match(self, crop):
        """crop → 中文动作词(过牌/加注/跟注/下注)或 None(无动作)。返回值直接喂 parse。"""
        r = match_hash(text_shape_hash(crop, self.sat_th, self.val_th, self.grid),
                       self.refs, self.threshold, self.margin)
        return r[0] if r else None
