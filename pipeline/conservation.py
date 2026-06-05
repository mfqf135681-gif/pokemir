"""pipeline/conservation.py — 整手守恒判级(与 DB 视图 v_hand_conservation 同口径)

纯逻辑、无 cv2/torch、Linux 可测。目的:给 §15 新桩基(reconstruct+solver)的回放
产出一个【与旧管线 DB 基准同口径】的守恒率,用旧的 1549 手当对比靶子,第一次看到
"新基础牢不牢"的系统级数字。

判级公式【逐字复刻】DB 视图 v_hand_conservation(pg_get_viewdef 抄回):
    chip_movement = Σ每座初始stack - Σ每座最终stack   (离开各座的总筹码,应≈pot)
    NULL_POT       : pot 为空 或 pot == 0
    OK             : -10 <= chip_movement <= pot*0.10 + 30
    CHECK_REQUIRED : 其余
旧管线在该口径下:OK 327 / CHECK 1153 / NULL 58(2026-06-04,n=1538 有 init/final stack)。

⚠️ 这只是【守恒】口径(底池=锚,整手对不对得平),不等于逐动作 recall/precision——
那要 --truth 真值文件。silent 24.6% 那套(v_ring_beam_pot_gaps)是为旧事件流设计的,
新 reconstruct 从 stack 跌幅反推动作 silent 天然≈0,不可比;新世界等价量=守恒残差。

自测: python pipeline/conservation.py
"""

OK = "OK"
CHECK = "CHECK_REQUIRED"
NULL_POT = "NULL_POT"
_BUCKETS = (OK, CHECK, NULL_POT)

LOWER_TOL = -10.0          # chip_movement 下界(允许少量负漂移)
_PCT = 0.10               # pot 的 10%
_FLAT = 30.0              # + 固定 30 容差

# 旧管线 DB 基准(v_hand_conservation @ 2026-06-04)——对比靶子,别改;复测请重抄。
OLD_BASELINE = {OK: 327, CHECK: 1153, NULL_POT: 58}


def upper_tol(pot):
    """OK 的 chip_movement 上界 = pot*10% + 30。"""
    return pot * _PCT + _FLAT


def conservation_status(chip_movement, pot):
    """单手判级,与视图 CASE 完全一致。pot None/0 → NULL_POT。"""
    if pot is None or pot == 0:
        return NULL_POT
    if LOWER_TOL <= chip_movement <= upper_tol(pot):
        return OK
    return CHECK


def chip_movement_from_stacks(stack_series, fuse=None):
    """Σ每座首个稳态 − Σ每座末个稳态(=派彩后)= 桌面净筹码变化(守恒手应≈rake≈0)。

    stack_series: {seat: [(t, val|None), ...]}。
    fuse: 可选平台融合函数(如 reconstruct.fuse_plateaus),传则用平台首末值抗噪;
          不传则取原始序列首末个非 None 读数。
    ⚠️ 末态【取派彩后】是对的:守恒口径是"桌面总量守恒"(cm≈rake),赢家那笔涨抵消别家的输。
       负 cm(Σ末>Σ初=桌面凭空多筹码)的真因不是派彩,而是**输家的下注没被末态 stack 反映**
       (漏抓→末态读高),与 recall 漏抓同源——见 change-log。
    与视图 sum_init/sum_final 对应(视图读 raw_data 存的 initial/final;本函数从时间序列重算)。
    """
    sum_init = 0.0
    sum_final = 0.0
    for obs in stack_series.values():
        if fuse:
            vals = [v for (_, v) in fuse(obs) if v is not None]
        else:
            vals = [v for (_, v) in obs if v is not None]
        if not vals:
            continue
        sum_init += vals[0]
        sum_final += vals[-1]
    return sum_init - sum_final


def summarize(records):
    """records: 可迭代 (chip_movement, pot) → {status: count}(含 0 桶)。"""
    counts = {b: 0 for b in _BUCKETS}
    for cm, pot in records:
        counts[conservation_status(cm, pot)] += 1
    return counts


def _pct(counts):
    tot = sum(counts.values()) or 1
    return {b: counts[b] * 100.0 / tot for b in _BUCKETS}


def format_comparison(new_counts, old_counts=None, new_label="新桩基(§15)", old_label="旧管线"):
    """并排打印 新桩基 vs 旧基准 的守恒桶占比。返回多行字符串。"""
    old_counts = OLD_BASELINE if old_counts is None else old_counts
    np_, op = _pct(new_counts), _pct(old_counts)
    nt, ot = sum(new_counts.values()), sum(old_counts.values())
    lines = [
        "=== 整手守恒对比(同口径 v_hand_conservation)===",
        f"  {'':<16}{old_label}(n={ot}){'':<6}{new_label}(n={nt})",
    ]
    name = {OK: "✅ OK(对得平)", CHECK: "⚠️ CHECK_REQUIRED", NULL_POT: "  NULL_POT"}
    for b in _BUCKETS:
        delta = np_[b] - op[b]
        sign = "+" if delta >= 0 else ""
        lines.append(f"  {name[b]:<16}{op[b]:>5.1f}% ({old_counts[b]:>4}){'':<4}"
                     f"{np_[b]:>5.1f}% ({new_counts[b]:>4})   Δ{sign}{delta:.1f}pt")
    # OK 率是头号指标:升=新桩基把更多手对平了
    lines.append(f"  → OK 率 {op[OK]:.1f}% → {np_[OK]:.1f}%  "
                 f"({'↑ 新桩基更牢' if np_[OK] > op[OK] else '↓ 退步,查' if np_[OK] < op[OK] else '持平'})")
    return "\n".join(lines)


def _self_test():
    # ① 判级:对着 DB 真实样本(含边界)逐条核——抄自 v_hand_conservation 实测行
    #    (cm, pot, 期望status)
    vectors = [
        (7, 896, OK), (-10, 590, OK), (2, 1110, OK), (76, 705, OK), (8, 230, OK),
        (800, 6420, CHECK), (-184, 1210, CHECK), (-35, 170, CHECK), (677, 246, CHECK),
        (300, 2650, CHECK),        # 边界:上界=265+30=295,300>295 → CHECK
        (-260, 6000, CHECK),       # 下界:-260 < -10 → CHECK(虽 |cm| 占 pot 极小)
        (-11, 100000, CHECK),      # 下界严格:-11 < -10 → CHECK
        (-10, 100000, OK),         # 下界含等号
        (0, None, NULL_POT), (61657, 0, NULL_POT),
    ]
    for cm, pot, exp in vectors:
        got = conservation_status(cm, pot)
        assert got == exp, f"cm={cm} pot={pot}: 期望 {exp} 得 {got}"
    print(f"✅ 判级核通过:{len(vectors)} 条(含边界/DB 实测样本)逐条复现视图 CASE")

    # ② chip_movement_from_stacks:两座,初始 500+300=800,末 287+150=437 → 363
    ss = {1: [(0, 500), (5, 287), (9, 287)], 2: [(0, 300), (5, 150)]}
    assert chip_movement_from_stacks(ss) == 363, chip_movement_from_stacks(ss)
    # 全 None 座跳过、不污染
    ss2 = {1: [(0, 500), (5, 200)], 3: [(0, None), (5, None)]}
    assert chip_movement_from_stacks(ss2) == 300, chip_movement_from_stacks(ss2)
    # 守恒口径:末态取【派彩后】是对的——赢家1000→900(下100)→1100(派彩),输家1000→900;
    # Σ初2000−Σ末(1100+900=2000)=0=守恒(rake≈0)。不可截派彩,否则守恒手被误判CHECK。
    ss_cons = {0: [(0, 1000), (5, 900), (9, 1100)], 1: [(0, 1000), (5, 900)]}
    assert chip_movement_from_stacks(ss_cons) == 0, chip_movement_from_stacks(ss_cons)
    print("✅ chip_movement 核通过:Σ首稳态−Σ末稳态(派彩后),空座跳过、守恒手 cm≈0")

    # ③ summarize + 桶计数
    recs = [(7, 896), (-184, 1210), (0, None), (76, 705)]
    c = summarize(recs)
    assert c == {OK: 2, CHECK: 1, NULL_POT: 1}, c
    print("✅ summarize 核通过:", c)
    print("\n" + format_comparison({OK: 600, CHECK: 800, NULL_POT: 50}))


if __name__ == "__main__":
    _self_test()
