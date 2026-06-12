"""砖0 端点链清洗 — 纯逻辑单测(Linux 可跑,无 DB/cv2)。

场景全部取材实战形态:rebuy 接缝(炸保险新手 18:33 爆仓重购)、读数嫌疑、
ante 时序 ±4 噪声、缺端点手、双信源归属一致/分歧(dfdb2245 型)。
"""
from solver.endpoint_chain import (
    CONTINUOUS, GAP_UNKNOWN, REBUY, SUSPECT_READ,
    HandPoint, attribute_winners, classify_seams, hand_residuals, rake_baseline,
)


def hp(hid, idx, player, initial, final, win_xx=None):
    return HandPoint(hand_id=hid, idx=idx, player=player,
                     initial=initial, final=final, win_xx=win_xx)


class TestSeams:
    def test_continuous_with_ante_noise(self):
        # 实测 ante 时序噪声 ±4 → tol 6 内判连续
        chain = [hp("h1", 0, "A", 500, 480), hp("h2", 1, "A", 484, 470)]
        s = classify_seams(chain)
        assert [x.kind for x in s] == [CONTINUOUS]
        assert s[0].gap == 4.0

    def test_rebuy_round_number(self):
        # 爆仓 → 下手整数补码(炸保险新手 11→null→重购 形态)
        chain = [hp("h1", 0, "A", 189, 11), hp("h2", 1, "A", 511, 480)]
        s = classify_seams(chain)
        assert s[0].kind == REBUY and s[0].gap == 500.0 and s[0].round_hint

    def test_rebuy_non_round_no_hint(self):
        chain = [hp("h1", 0, "A", 100, 80), hp("h2", 1, "A", 317, 300)]
        s = classify_seams(chain)
        assert s[0].kind == REBUY and not s[0].round_hint

    def test_suspect_read(self):
        # 筹码凭空变少 = 某端读数嫌疑(正规局不许取筹下桌)
        chain = [hp("h1", 0, "A", 500, 480), hp("h2", 1, "A", 300, 280)]
        assert classify_seams(chain)[0].kind == SUSPECT_READ

    def test_missing_endpoint(self):
        chain = [hp("h1", 0, "A", 500, None), hp("h2", 1, "A", 480, 470)]
        s = classify_seams(chain)
        assert s[0].kind == GAP_UNKNOWN and s[0].gap is None

    def test_sources_traceable(self):
        # 圈梁纪律5:每条结论可追溯到真值
        chain = [hp("hand-aaaa-1111", 0, "A", 500, 480),
                 hp("hand-bbbb-2222", 1, "A", 480, 470)]
        assert classify_seams(chain)[0].sources == ["final@hand-aaa", "initial@hand-bbb"]


class TestResiduals:
    def test_rake_candidate(self):
        # 三人手:赢家 +90、输家 -50/-44 → Σ=-4 = rake 候选
        pts = {"h1": [hp("h1", 0, "A", 500, 590), hp("h1", 0, "B", 300, 250),
                      hp("h1", 0, "C", 200, 156)]}
        r = hand_residuals(pts)["h1"]
        assert r["sum_net"] == -4.0 and r["rake_candidate"]

    def test_missing_seat_disqualifies(self):
        # 缺座的钱去向不明 → 不许当 rake 样本(不强推)
        pts = {"h1": [hp("h1", 0, "A", 500, 590), hp("h1", 0, "B", 300, None)]}
        r = hand_residuals(pts)["h1"]
        assert r["n_missing"] == 1 and not r["rake_candidate"]

    def test_positive_sum_not_rake(self):
        # Σnet 大于容忍(+50)= 凭空多钱(rebuy/误读)≠ rake
        pts = {"h1": [hp("h1", 0, "A", 500, 600), hp("h1", 0, "B", 300, 250)]}
        assert not hand_residuals(pts)["h1"]["rake_candidate"]

    def test_rake_baseline_cold_start(self):
        # 样本不足 → None(冷启动不硬给数)
        pts = {"h1": [hp("h1", 0, "A", 500, 496)]}
        assert rake_baseline(hand_residuals(pts), min_samples=20) is None

    def test_rake_baseline_distribution(self):
        pts = {f"h{i}": [hp(f"h{i}", i, "A", 500, 500 - 4 - (i % 3))] for i in range(30)}
        bl = rake_baseline(hand_residuals(pts), min_samples=20)
        assert bl is not None and bl["n"] == 30 and 4 <= bl["median"] <= 6


class TestAttribution:
    def test_agree(self):
        pts = [hp("h1", 0, "A", 500, 590, win_xx=90.0), hp("h1", 0, "B", 300, 210)]
        r = attribute_winners(pts, pot=100)
        assert r["winners"] == ["A"] and r["agree_xx"] is True and not r["flags"]

    def test_disagree_marked_not_judged(self):
        # dfdb2245 型:+xx 只见 A,端点说 A、B 都赚(边池/保险)→ 只标分歧
        pts = [hp("h1", 0, "A", 500, 560, win_xx=60.0), hp("h1", 0, "B", 300, 330)]
        r = attribute_winners(pts, pot=100)
        assert r["winners"] == ["A", "B"] and r["agree_xx"] is False

    def test_won_exceeds_pot_flag(self):
        # 赢额合计 > pot = rebuy/误读混入 → flag 交 seam 解释
        pts = [hp("h1", 0, "A", 500, 800), hp("h1", 0, "B", 300, 290)]
        r = attribute_winners(pts, pot=100)
        assert "won_exceeds_pot" in r["flags"]

    def test_no_signal(self):
        pts = [hp("h1", 0, "A", 500, 498), hp("h1", 0, "B", 300, 299)]
        r = attribute_winners(pts, pot=50)
        assert r["winners"] == [] and r["agree_xx"] is None


class TestPairOutliers:
    def test_misread_middle_hand(self):
        # 854366 实锤型: -404 后 +552 互抵 → 中间手端点离群
        from solver.endpoint_chain import pair_outliers
        chain = [hp("h1", 0, "A", 900, 880), hp("h2", 1, "A", 476, 460),
                 hp("h3", 2, "A", 1012, 1000)]
        seams = classify_seams(chain)
        outs = pair_outliers(seams)
        assert len(outs) == 1 and outs[0]["hand"] == "h2"

    def test_true_rebuy_not_flagged(self):
        # 真补码: 大正 gap 后链继续连续 → 无反号对,不标
        from solver.endpoint_chain import pair_outliers
        chain = [hp("h1", 0, "A", 100, 20), hp("h2", 1, "A", 520, 480),
                 hp("h3", 2, "A", 478, 430)]
        assert pair_outliers(classify_seams(chain)) == []

    def test_non_cancelling_not_flagged(self):
        # 反号但不互抵(-300 后 +30)→ 不是单点离群,不标
        from solver.endpoint_chain import pair_outliers
        chain = [hp("h1", 0, "A", 900, 880), hp("h2", 1, "A", 580, 560),
                 hp("h3", 2, "A", 590, 550)]
        assert pair_outliers(classify_seams(chain)) == []

    def test_dup_in_hand(self):
        # 半截首手同名占两座(时来运转转型)→ DUP 标记不算 gap
        from solver.endpoint_chain import DUP_IN_HAND
        chain = [hp("h1", 0, "A", 500, 480), hp("h1", 0, "A", 300, 290),
                 hp("h2", 1, "A", 480, 470)]
        s = classify_seams(chain)
        assert s[0].kind == DUP_IN_HAND and s[0].gap is None
