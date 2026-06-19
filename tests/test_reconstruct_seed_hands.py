"""#226 守恒求解器【种子测试集】——4 手实战 live 数据(2026-06-10 75min 局)。

来源:对该局逐手复盘(审慎复盘 4 手),发现 **逐动作层(per-action)噪声极重**而
**端点层(per-seat 初/终栈)可靠**。这 4 手专门覆盖逐动作层的主要失败模式,作为:
  (1) 端点层 reconstruct_hand_chips 的【回归护栏】——证明它在真实脏数据上仍判对赢家 + flag 异常;
  (2) 未来 #226 求解器(per-action 候选 → 端点否决 → 合法序列)的【验收目标】(见 TestSolverTargets)。

每手的端点真相 + 逐动作病灶都已标注。**端点 = 唯一 ground-truth 锚**(本身偶有单点掉位,
靠守恒 flag 自暴露)。逐动作层的"全下安错人/赢家记成弃牌/底池超读 10×/金额缺"都是求解器要治的。
纯逻辑,Linux 可单测。
"""
import pytest

from pipeline.reconstruct import reconstruct_hand_chips

BLINDS = dict(sb=2, bb=4, ante=4)

# ─────────────────────────────────────────────────────────────────────────
# 4 手种子:每手 = 端点(initial/final/pot)+ 端点真相 + 逐动作病灶 + #243 stackzero 判定
# ─────────────────────────────────────────────────────────────────────────
SEED_HANDS = [
    {
        "id": "4c736d62",  # 14:02, pot读1984
        "initial": {0: 533, 1: 1405, 2: 1010, 3: 546, 4: 637, 5: 634, 6: 790, 7: 124},
        "final":   {0: 1039, 1: 860, 2: 1006, 3: 542, 4: 629, 5: 630, 6: 782, 7: 120},
        "pot": 1984,
        # 端点真相:s0(水上)全下~533 翻倍赢;s1(罗湖)call 后输545。逻辑铁证:s0只有533,
        # 赢506 → s0 才是全下者(s0 无力 call 1401)。
        "truth_winner": 0,
        "truth_note": "s0(水上)全下~533赢+506;s1(罗湖)输545。真底池~577,非1984。",
        "per_action_defects": [
            "记『罗湖(s1)全下1401』= 假(罗湖终栈860,没全下)",
            "真全下者 s0(水上)被记成『弃牌』",
            "罗湖全下被重复记一次(seq23 Allin IUL)",
            "全下玩家罗湖被记幻觉弃牌(seq24)",
            "全下额记成 1(OCR读 'Allin 1' 的 1)",
        ],
        "stackzero_fired": {"seat": 1, "amt": 1401, "verdict": "FALSE"},  # #243 假阳
        "expect": {"winners": [0], "conservation_ok": True, "pot_suspect": True},
        # 注:conservation_ok=True 是被超读 pot(1984)抬高 rake_ceiling 掩盖的;
        #     真 pot~577 时 rake_ceiling 会收紧到 ~46 → Σnet=-71 应 flag excess_loss。
        #     『pot 超读掩盖守恒检查』本身是求解器要治的耦合(见 TestSolverTargets)。
    },
    {
        "id": "e7a420e4",  # 14:09, pot读1467
        "initial": {0: 581, 1: 1653, 2: 1282, 3: 438, 4: 491, 5: 880, 6: 245, 7: 692},
        "final":   {1: 1647, 2: 1919, 3: 398, 4: 487, 5: 876, 6: 241, 7: 686},  # s0 离场/没了
        "pot": 1467,
        "truth_winner": 2,
        "truth_note": "s2(Xianshou)赢+637;s0(水上)turn全下339被打爆离场(终栈缺)。",
        "per_action_defects": [
            "记『水上(s0)turn弃牌』= 假(水上真全下339后爆,非弃)",
            "赢家 Xianshou(s2)被记幻觉弃牌(seq36)",
        ],
        "stackzero_fired": {"seat": 0, "amt": 339, "verdict": "TRUE"},  # #243 真catch(OCR漏)
        # s0 不在 final → partial_seats + 守恒破(缺一个输家的钱 → 看似凭空+573)
        "expect": {"winners": [2], "conservation_ok": False, "partial_seats": True},
    },
    {
        "id": "50f2abe5",  # 13:33, pot读1121,普通多人到摊牌(无全下)
        "initial": {0: 555, 1: 1121, 2: 853, 3: 173, 4: 958, 5: 664, 6: 396},
        "final":   {0: 551, 1: 1113, 2: 1385, 3: 165, 4: 952, 5: 14, 6: 388},
        "pot": 1121,
        "truth_winner": 2,
        "truth_note": "s2(Xianshou)赢+532;s5(点纹香)输~650。s5终栈14疑掉位(真~140→rake才正常)。",
        "per_action_defects": [
            "下注/跟注 amount 几乎全 null(只抓形状不抓金额)",
            "河牌结果未记(末事件是点纹香河牌下注,谁赢/跟多少都缺)",
            "s5 终栈 14 疑数字掉位(应~140)→ 守恒 flag excess_loss 自暴露",
        ],
        "stackzero_fired": None,
        # 注:这手【没有幻觉弃牌、赢家没被误判】——失败集中在金额,弃牌attribution干净。
        "expect": {"winners": [2], "conservation_ok": False, "excess_loss": True,
                   "pot_plausible": True},
    },
    {
        "id": "953df25c",  # 13:56, pot读4439(全场最大),sz_only
        "initial": {1: 1507, 2: 1102, 3: 458, 5: 954, 6: 483, 7: 89},
        "final":   {1: 1503, 2: 1066, 3: 684, 5: 948, 6: 386, 7: 50},
        "pot": 4439,
        "truth_winner": 3,
        "truth_note": "s3(一木)赢+226。真底池~250,记的4439超读~10×。",
        "per_action_defects": [
            "底池超读~10×(记4439,真~250)",
        ],
        "stackzero_fired": {"seat": 7, "amt": 57, "verdict": "FALSE"},  # #243 假阳(东胜终栈50,没全下)
        "expect": {"winners": [3], "pot_suspect": True},
    },
]


def _run(hand):
    return reconstruct_hand_chips(hand["initial"], hand["final"], pot=hand["pot"], **BLINDS)


# ─── 端点层回归护栏:reconstruct_hand_chips 在真实脏数据上的纠错 ───────────────
class TestEndpointLayer:
    @pytest.mark.parametrize("hand", SEED_HANDS, ids=[h["id"] for h in SEED_HANDS])
    def test_winner_matches_endpoint_truth(self, hand):
        """端点层判对赢家——即使逐动作层把赢家记成了弃牌(A 手水上 / B 手)。"""
        r = _run(hand)
        assert r["winners"] == hand["expect"]["winners"], (
            f"{hand['id']}: 赢家应 {hand['expect']['winners']}({hand['truth_note']}),"
            f"实得 {r['winners']}")

    @pytest.mark.parametrize("hand", SEED_HANDS, ids=[h["id"] for h in SEED_HANDS])
    def test_conservation_and_flags(self, hand):
        """守恒/异常 flag 该按预期触发:A/D 超读底池→pot_suspect;B 缺座→partial;C 掉位→excess_loss。"""
        r = _run(hand)
        exp = hand["expect"]
        flags_blob = " ".join(r["flags"])
        if "conservation_ok" in exp:
            assert r["conservation_ok"] is exp["conservation_ok"], \
                f"{hand['id']}: conservation_ok 应 {exp['conservation_ok']},实 {r['conservation_ok']} flags={r['flags']}"
        if exp.get("pot_suspect"):
            assert "pot_suspect" in flags_blob, f"{hand['id']}: 应 flag pot_suspect,实 {r['flags']}"
        if exp.get("pot_plausible"):
            assert r["pot_plausible"] == hand["pot"], f"{hand['id']}: pot 应判 plausible,实 {r}"
        if exp.get("partial_seats"):
            assert "partial_seats" in flags_blob, f"{hand['id']}: 应 flag partial_seats,实 {r['flags']}"
        if exp.get("excess_loss"):
            assert "excess_loss" in flags_blob, f"{hand['id']}: 应 flag excess_loss,实 {r['flags']}"


# ─── 求解器验收目标(#226 未建成 → xfail 占位,建成后逐条转绿)──────────────────
class TestSolverTargets:
    """#226 求解器验收目标。
    端点否决(假全下/赢家误弃)的验收已迁至 tests/test_endpoint_veto.py(canonical 实现 =
    solver.veto_events_by_endpoint 元组原生 + build_facts 整合);此处只留规则③(补漏真全下)待办。"""

    @pytest.mark.xfail(reason="#226 规则③(补漏真全下)未建:OCR漏的真全下需端点+stackzero补回", strict=False)
    def test_recovers_missed_allin_B_water(self):
        """B 手:水上真全下339被记弃牌(OCR漏)→ 求解器应据端点(爆离场)+stackzero 补回全下。"""
        raise AssertionError("规则③未实现:应补回 s0 turn all-in 339")
