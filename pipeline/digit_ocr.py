"""pipeline/digit_ocr.py — 定宽数字识别纯核(无 cv2,可离线单测)。

固定字体 + 有缝 → 投影切割 + 逐格模板匹配(§Option2)。本模块只做:
  ① segment_cells:列墨色投影 → 数字格(墨色连续段);窄 "1" 天然 handle。
  ② parse_number:逐格分类(分类器注入)→ 抽连续数字串 + 标记(前导 "+"、末尾 "%"=all-in、
     无法识别 "?"=筹码图标等)。
匹配器(crop + cv2.matchTemplate)在识别层注入(Win);本核纯逻辑,mock 分类器即可测。

字符集按语境:stack(0-9,all-in 时 +%)/ pot / 上街池(0-9)/ bet(0-9,前导筹码图标)/
+xx(0-9,前导 "+")/ 胜率(0-9 + %,在 stack 区)。
"""


def segment_cells(col_ink, gap_th=0, min_gap=2, min_cell_w=3):
    """列墨色数组 → 数字格 [(x0, x1), ...]。
    gap_th: 墨色 > 此 = 有墨。
    min_gap: 相邻墨色段间缝 < 此列数 → 合并(治数字内 1px 细缝把一字劈两半)。
    min_cell_w: 格宽 < 此 → 丢弃(治 1-2px 噪点碎片)。
    实测真实字形:gap_th=0/min_gap=2/min_cell_w=3 → 段1 多座 4-5 位数切对位数。
    """
    runs = []
    start = None
    for x, ink in enumerate(col_ink):
        if ink > gap_th:
            if start is None:
                start = x
        elif start is not None:
            runs.append([start, x - 1]); start = None
    if start is not None:
        runs.append([start, len(col_ink) - 1])
    # 合并窄缝(< min_gap)分隔的段 = 同一数字
    merged = []
    for r in runs:
        if merged and (r[0] - merged[-1][1] - 1) < min_gap:
            merged[-1][1] = r[1]
        else:
            merged.append(r[:])
    # 丢弃窄格(噪点)
    return [(a, b) for a, b in merged if (b - a + 1) >= min_cell_w]


def parse_number(cells, classify, min_cell_w=1):
    """cells → (digits, flags)。classify((x0,x1)) → 单字符:'0'-'9' / '+' / '%' / '?'(图标/未知)。

    抽出连续数字串(顺序保留);标记:
      has_plus  前导/含 "+"(+xx)
      has_pct   含 "%"(=all-in,该数字是胜率非筹码)
      has_icon  含 "?"(筹码图标等非数字,bet 区前导)
      raw       逐格识别原串(调试用)
    太窄的格(< min_cell_w)丢弃为噪声。
    """
    chars = [classify((x0, x1)) for (x0, x1) in cells if (x1 - x0 + 1) >= min_cell_w]
    digits = "".join(c for c in chars if c.isdigit())
    flags = {
        "has_plus": "+" in chars,
        "has_pct": "%" in chars,
        "has_icon": "?" in chars,
        "raw": "".join(chars),
    }
    return digits, flags


def _self_test():
    # ① 切割(默认 min_gap=2 / min_cell_w=3)
    # 两数字,真缝(≥2列)分隔,各≥3px:
    assert segment_cells([0, 0, 5, 5, 5, 0, 0, 6, 6, 6, 0]) == [(2, 4), (7, 9)]
    # 数字内 1px 细缝 → 合并(治 seat7 劈裂):
    assert segment_cells([5, 5, 5, 0, 5, 5, 5]) == [(0, 6)]
    # 1px 噪点碎片 → 丢弃(治 seat3/5):
    assert segment_cells([5, 5, 5, 0, 0, 9, 0, 0]) == [(0, 2)]
    # 窄"1"(3px)保留:
    assert segment_cells([0, 5, 5, 5, 0, 0, 9, 9, 9, 0]) == [(1, 3), (6, 8)]
    print("✅ segment_cells:真缝切/细缝合并/噪点丢/窄1留 OK")

    # ② parse:mock 分类器按 cell 顺序吐预设字符
    def mk(seq):
        it = iter(seq)
        return lambda cell: next(it)

    # 纯数字 1538
    d, f = parse_number([(0, 4), (6, 10), (12, 16), (18, 22)], mk("1538"))
    assert d == "1538" and not f["has_plus"] and not f["has_pct"], (d, f)
    # bet:前导筹码图标 + 28
    d, f = parse_number([(0, 8), (10, 14), (16, 20)], mk("?28"))
    assert d == "28" and f["has_icon"], (d, f)
    # +xx:前导 + 45
    d, f = parse_number([(0, 3), (5, 9), (11, 15)], mk("+45"))
    assert d == "45" and f["has_plus"], (d, f)
    # 胜率 31%:末尾 % → all-in 信号
    d, f = parse_number([(0, 4), (6, 8), (10, 14)], mk("31%"))
    assert d == "31" and f["has_pct"], (d, f)
    print("✅ parse_number:数字串 + 标记(+/%/图标)OK")

    print("\n✅ digit_ocr 纯核自测全过")


if __name__ == "__main__":
    _self_test()
