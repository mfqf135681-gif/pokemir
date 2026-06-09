"""pipeline/digit_ocr.py — 定宽数字识别纯核(无 cv2,可离线单测)。

固定字体 + 有缝 → 投影切割 + 逐格模板匹配(§Option2)。本模块只做:
  ① segment_cells:列墨色投影 → 数字格(墨色连续段);窄 "1" 天然 handle。
  ② parse_number:逐格分类(分类器注入)→ 抽连续数字串 + 标记(前导 "+"、末尾 "%"=all-in、
     无法识别 "?"=筹码图标等)。
匹配器(crop + cv2.matchTemplate)在识别层注入(Win);本核纯逻辑,mock 分类器即可测。

字符集按语境:stack(0-9,只有筹码)/ pot / 上街池(0-9)/ bet(0-9,前导筹码图标)/
+xx(0-9,前导 "+")/ 胜率(0-9 + %,在筹码【下方独立区】,非 stack 区;待另框 zone)。
  注:has_pct 仍是通用 flag(将来真胜率%-区复用),但 stack 区不含 %(R15 修正 2026-06-09)。
"""


def segment_cells(col_ink, gap_th=0, min_gap=2, min_cell_w=3, max_merge_w=14):
    """列墨色数组 → 数字格 [(x0, x1), ...]。
    gap_th: 墨色 > 此 = 有墨。
    min_gap: 相邻墨色段间缝 < 此列数 → 合并(治数字内 1px 细缝把一字劈两半)。
    max_merge_w: 仅当【合并后宽度 ≤ 此】才合并 → 区分"字内细缝(并出≤14px)"与"字间 1px 真缝
        (并出 ~24px=两位)"。实测根因:4021/455 的 4 右缝=1px,旧逻辑误并两位为怪格。
    min_cell_w: 格宽 < 此 → 丢弃(治 1-2px 噪点碎片)。
    实测真实字形:单数字 ~9-12px,两位并出 ~24px → max_merge_w=14 干净分界。
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
    # 合并窄缝(< min_gap)分隔的段 = 同一数字;但合并后宽度超 max_merge_w = 误并两位 → 不并
    merged = []
    for r in runs:
        if (merged and (r[0] - merged[-1][1] - 1) < min_gap
                and (r[1] - merged[-1][0] + 1) <= max_merge_w):
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
    # 字间 1px 真缝:两个 12px 宽段,缝 1px → 并出 25px>14 → 不并(治 4021/455 误并两位):
    wide = [0] + [5] * 12 + [0] + [5] * 12 + [0]  # 段(1,12) 段(14,25),缝=1px
    assert segment_cells(wide) == [(1, 12), (14, 25)], segment_cells(wide)
    # 字内 1px 细缝:两个小段并出 ≤14px → 照并(不被 max_merge_w 误伤):
    assert segment_cells([0, 5, 5, 5, 5, 0, 5, 5, 5, 5, 0]) == [(1, 9)]
    print("✅ segment_cells:真缝切/细缝合并/噪点丢/窄1留/宽度上限防误并两位 OK")

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
