"""tools/active_set.py — 活跃集判定纯逻辑(无 cv2,可离线单测)。

phash hamming 的【计算】在 replay(_avg_hash/_hamming,依赖 cv2,Win 端);
本模块只吃"每帧对参考的 hamming(或任意在手布尔)"数列,产出:
  - 每座的【在手区间】(持久态,抗单帧抖动)
  - 某时刻的【活跃集】

供 L1 活跃集基桩消费,喂 invariants P-6(末位跟注=注级,需"翻后仍活跃")
/ P-7(活跃集=有牌角标记的座)。检测判据(phash 对参考 / 白占比 / 其他)无论用哪种,
最终都落到"每帧在手布尔",故本逻辑与 UI/判据无关、可复用。
"""


def active_intervals(samples, th, min_run=2):
    """单座"对参考 hamming"时间序列 → 在手区间。

    samples: [(t, hamming | None)]  (None = 该帧读不到,按非在手)
    th: hamming ≤ th 视为"像参考"(有牌 = 在手)
    min_run: 连续 ≥ 此帧数才算一段在手(滤单帧抖动)

    返回 [(t_start, t_end), ...]。
    """
    intervals = []
    run_start = None
    run_len = 0
    last_t = None
    for (t, ham) in samples:
        present = (ham is not None and ham <= th)
        if present:
            if run_start is None:
                run_start, run_len = t, 1
            else:
                run_len += 1
            last_t = t
        else:
            if run_start is not None and run_len >= min_run:
                intervals.append((run_start, last_t))
            run_start, run_len = None, 0
    if run_start is not None and run_len >= min_run:
        intervals.append((run_start, last_t))
    return intervals


def active_set_at(t, per_seat_intervals):
    """某时刻 t 的活跃集 = 在手区间覆盖 t 的座集合。

    per_seat_intervals: {seat: [(t_start, t_end), ...]}
    返回 set[seat]。
    """
    return {seat for seat, ivs in per_seat_intervals.items()
            if any(s <= t <= e for (s, e) in ivs)}
