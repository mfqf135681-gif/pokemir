r"""pipeline/digit_reader.py — 可复用模板数字读取器(配方:归一+列墨+多样本相关器)。

把 `tools/digit_probe.py` 里【埋在 CLI 的】采模板/匹配逻辑抽成可复用件,供管线兜底读。
切割/解析复用 `digit_ocr`(Linux 单测);**cv2/numpy 部分(灰度/归一/列墨/相关器)忠实
复制自 digit_probe,Win-only 未在 Linux 验**(见 [[dev-rule-validate-blind-spots]])。

设计意图(2026-06-05):EasyOCR 的文字检测(CRAFT)会把**孤立单个 "0"**(全下玩家
stack=0)当噪点丢 → 读 None;本读取器用列墨切割(无检测步)能命中,作 `read_stack`
**兜底**(EasyOCR 读空时才用,不碰 EasyOCR 读得好的)。

纯 decode 逻辑(cells→int / None)Linux 可测;`read(crop)` 的 cv2 预处理 Win 验。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import digit_ocr  # noqa: E402  纯切割/解析核(已单测)

# 配方默认旋钮(与 digit_probe 一致)
INK_TH = 150
SCORE_TH = 0.6
GAP_TH = 0
MIN_GAP = 2
MIN_CELL_W = 3
MAX_MERGE_W = 14


# ── cv2/numpy 部分(忠实复制自 digit_probe.gray_roi / _col_ink / _corr / _match_char_multi)──
# ⚠️ Win-only:无 cv2/numpy 的机器(本 Linux 开发机)无法验证。
def _gray_normalize(crop, normalize=True):
    import cv2
    import numpy as np
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    if normalize:  # p2–p98 拉满量程(治暗黄字卡阈值;digit_probe 默认开)
        lo, hi = float(np.percentile(g, 2)), float(np.percentile(g, 98))
        if hi - lo >= 30:
            g = ((g.astype(np.float32) - lo) / (hi - lo) * 255).clip(0, 255).astype(np.uint8)
    return g


def _col_ink(gray, ink_th=INK_TH):
    import numpy as np
    return (gray > ink_th).astype(np.uint8).sum(axis=0).tolist()


def _corr(glyph, tmpl):
    """去均值归一相关(glyph resize 到 tmpl 尺寸);与 digit_probe._corr 同度量。"""
    import cv2
    import numpy as np
    g = cv2.resize(glyph, (tmpl.shape[1], tmpl.shape[0])).astype(np.float32)
    tf = tmpl.astype(np.float32)
    g -= g.mean()
    tf -= tf.mean()
    denom = float(np.linalg.norm(g) * np.linalg.norm(tf)) or 1.0
    return float((g * tf).sum() / denom)


class DigitReader:
    """多样本模板数字读取器。exemplars = {char: [glyph(np.uint8 2D), ...]}(跨座汇入同 pool)。"""

    def __init__(self, exemplars=None, score_th=SCORE_TH, ink_th=INK_TH, normalize=True):
        self.exemplars = exemplars or {}
        self.score_th = score_th
        self.ink_th = ink_th
        self.normalize = normalize

    # —— 纯 decode(无 cv2;Linux 可测):cells + classify → int|None ——
    def _decode(self, cells, classify):
        """cells→数字串(借 digit_ocr.parse_number)。含图标'?'/胜率'%'/空 → None(兜底拒读,
        保持 EasyOCR 的空,不瞎补)。"""
        digits, flags = digit_ocr.parse_number(cells, classify, min_cell_w=1)
        if not digits or flags["has_icon"] or flags["has_pct"]:
            return None
        try:
            return int(digits)
        except ValueError:
            return None

    def _match(self, glyph):
        """多样本:每字取其所有样本最高分,选最高字;< score_th → '?'。"""
        best, best_s = "?", -1.0
        for ch, gls in self.exemplars.items():
            s = max(_corr(glyph, t) for t in gls)
            if s > best_s:
                best, best_s = ch, s
        return best if best_s >= self.score_th else "?"

    def read(self, crop):
        """crop(BGR 或灰度 ndarray)→ int|None。⚠️ cv2 路径 Win-only。
        无模板 → None(安全默认:不接管 = 等于没兜底)。"""
        if not self.exemplars:
            return None
        if crop is None or getattr(crop, "size", 0) == 0:
            return None
        g = _gray_normalize(crop, self.normalize)
        cells = digit_ocr.segment_cells(_col_ink(g, self.ink_th),
                                        GAP_TH, MIN_GAP, MIN_CELL_W, MAX_MERGE_W)
        if not cells:
            return None
        return self._decode(cells, lambda c: self._match(g[:, c[0]:c[1] + 1]))

    # —— 持久化(glyph 存 2D int 列表)——
    def save(self, path):
        obj = {
            "score_th": self.score_th,
            "ink_th": self.ink_th,
            "normalize": self.normalize,
            "exemplars": {ch: [gl.tolist() for gl in gls] for ch, gls in self.exemplars.items()},
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    @classmethod
    def load(cls, path):
        import numpy as np
        d = json.loads(open(path, encoding="utf-8").read())
        ex = {ch: [np.array(gl, dtype=np.uint8) for gl in gls]
              for ch, gls in d["exemplars"].items()}
        return cls(ex, d.get("score_th", SCORE_TH), d.get("ink_th", INK_TH),
                   d.get("normalize", True))


def _self_test():
    """Linux 可测部分:_decode 的 cells→int / None 判定(classify 用假分类器,绕开 cv2)。"""
    r = DigitReader(exemplars={"x": [None]})  # 占位,使 read 不短路;decode 不碰 exemplars

    def mk(seq):
        it = iter(seq)
        return lambda cell: next(it)

    # 纯数字串
    assert r._decode([(0, 4), (6, 10), (12, 16)], mk("128")) == 128
    # 孤立单 "0"(全下场景核心)
    assert r._decode([(0, 9)], mk("0")) == 0
    # 含图标 '?' → None(拒读,不瞎补)
    assert r._decode([(0, 8), (10, 14)], mk("?5")) is None
    # 含胜率 '%' → None(那是胜率非筹码)
    assert r._decode([(0, 4), (6, 8)], mk("3%")) is None
    # 全非数字 / 空 → None
    assert r._decode([(0, 4)], mk("?")) is None
    assert r._decode([], mk("")) is None
    # 无模板 → read 直接 None(安全默认)
    assert DigitReader().read(object()) is None
    print("✅ digit_reader._decode:数字串/孤立0/图标拒/胜率拒/空拒/无模板安全 OK")
    print("⚠️ cv2 路径(_gray_normalize/_col_ink/_corr/_match/read 全程)Win-only 未验")


if __name__ == "__main__":
    _self_test()
