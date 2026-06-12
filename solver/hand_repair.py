"""solver/hand_repair.py — 砖1:逐手补账(圈梁 D1/D5/D7 = J-1 实装,142 型缺口自动求解)。

核心恒等式(全部来自已验信号,无新识别):
  C1 手级守恒: antes + Σ(player,street)街累计投入 = pot_final(显示口径,含未跟注退还前)
  C2 输家端点: 输家拿不回钱、rake 不从他身上扣 → 投入_p = -net_p【精确】
  C2′ 赢家端点: net_w = (pot - rake - 退还) - 投入_w → 投入_w 可由端点+rake基线反推(弱一档)
  C4 跟注语义: 跟注者本街累计 = 本街最高注(短 all-in 例外)→ 缺口的街落位依据

求解(MVP,锁圈梁 8 维不扩):
  1. gap = pot_final - 已记录总投入;|gap|≤tol → EXACT(对照组绝不补,防误伤);
  2. 每个输家 deficit = (-net) - 已记录投入;Σdeficit ≈ gap(2+ 印证:端点×守恒)→ 补 MISSING_CONTRIB;
  3. 残余 → 唯一赢家侧用 C2′+rake 基线再印证一道;仍不平 → UNSOLVED(进解冻登记簿,不硬凑);
  4. deficit < -tol = 记录比端点多 → AMOUNT_OVERREAD 标记(v1 只标不出修正值)。
街落位:按行动序走他在场的街,"补到本街最高注"贪心分配;分不完落最后在场街并标 street_uncertain。

纪律(solver/__init__ 继承清单):每修 ≥2 印证;推断深度=1(全部修复直接从原始约束出,
互不为输入);只产出独立报告,不碰 action_events;补不平自动产出登记簿行。
"""
from __future__ import annotations

from dataclasses import dataclass, field

STREETS = ["preflop", "flop", "turn", "river"]

EXACT = "EXACT"          # 原账全对(|gap|≤tol),不许补
REPAIRED = "REPAIRED"    # 补后 |残余|≤tol+rake 容忍
UNSOLVED = "UNSOLVED"    # 补不平 → 解冻登记簿

MISSING_CONTRIB = "MISSING_CONTRIB"    # 漏记投入(142 型)
AMOUNT_OVERREAD = "AMOUNT_OVERREAD"    # 记录金额比端点能支持的多(毒值残余型)


@dataclass
class HandFacts:
    """一手的全部输入事实(只读,来自 DB 已存数据)。金额=街累计口径。"""
    hand_id: str
    pot_final: float | None
    antes: dict[str, float]                          # player → ante
    street_contrib: dict[tuple[str, str], float]     # (player, street) → 街累计(含盲注/all_in)
    nets: dict[str, float]                           # player → 端点净额(有端点读数的)
    xx_winners: set[str] = field(default_factory=set)
    folded_street: dict[str, str] = field(default_factory=dict)  # player → 弃牌街(没弃则缺)

    def recorded(self, p: str) -> float:
        return self.antes.get(p, 0.0) + sum(
            v for (pl, _st), v in self.street_contrib.items() if pl == p)

    def recorded_total(self) -> float:
        return sum(self.antes.values()) + sum(self.street_contrib.values())


@dataclass
class Repair:
    player: str
    kind: str                       # MISSING_CONTRIB / AMOUNT_OVERREAD
    delta: float                    # 缺/超多少(正数)
    streets: list = field(default_factory=list)   # [(street, add_amount)] 落位;可空
    street_uncertain: bool = False
    corroborators: list = field(default_factory=list)  # ≥2 才合法(纪律1)


@dataclass
class RepairReport:
    hand_id: str
    status: str
    gap_before: float | None
    gap_after: float | None
    repairs: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def ledger_row(self) -> str | None:
        """UNSOLVED → contracts/recognition-freeze.md §5 登记簿行(解冻保险条款喂料)。"""
        if self.status != UNSOLVED:
            return None
        return (f"| (auto) | {self.hand_id[:8]} | unsolvable | "
                f"gap_after={self.gap_after} notes={';'.join(self.notes) or '-'} | open |")


def _assign_streets(facts: HandFacts, player: str, need: float, tol: float):
    """缺口按"补到本街最高注"贪心落街(C4)。返回 ([(street, add)], 是否不确定)。"""
    assigns, remaining = [], need
    fold_st = facts.folded_street.get(player)
    fold_idx = STREETS.index(fold_st) if fold_st in STREETS else len(STREETS) - 1
    street_max = {st: max((v for (pl, s), v in facts.street_contrib.items() if s == st),
                          default=0.0) for st in STREETS}
    last_active = STREETS[0]
    for i, st in enumerate(STREETS):
        if i > fold_idx:
            break
        last_active = st
        cur = facts.street_contrib.get((player, st), 0.0)
        gap_to_match = street_max[st] - cur
        if gap_to_match > tol and remaining > tol:
            add = min(gap_to_match, remaining)
            assigns.append((st, round(add, 1)))
            remaining = round(remaining - add, 1)
    uncertain = False
    if remaining > tol:
        # 分不完(他本人是本街最高注者/街最高注本身漏)→ 余量落最后在场街,标不确定
        assigns.append((last_active, round(remaining, 1)))
        uncertain = True
    # 同街多条合并(贪心+余量可能落同一街,报告里别出现 "preflop:52,preflop:104")
    merged: dict[str, float] = {}
    for st, a in assigns:
        merged[st] = round(merged.get(st, 0.0) + a, 1)
    return list(merged.items()), uncertain


def solve_hand(facts: HandFacts, tol: float = 6.0, rake_allow: float = 30.0) -> RepairReport:
    """逐手求解。rake_allow:残余允许落在 [ -rake_allow, tol ](rake 只会让 pot 比净流入多)。"""
    rep = RepairReport(hand_id=facts.hand_id, status=UNSOLVED, gap_before=None, gap_after=None)
    if facts.pot_final is None:
        rep.notes.append("pot_final 缺")
        return rep
    gap = round(facts.pot_final - facts.recorded_total(), 1)
    rep.gap_before = gap

    # ① 对照组保护:原账已平 → 绝不补(防误伤 EXACT 手)
    if abs(gap) <= tol:
        rep.status, rep.gap_after = EXACT, gap
        return rep

    # ② 输家精确目标(C2):投入 = -net;赢家(净>0 或 +xx 在列)不进此路
    deficits: dict[str, float] = {}
    for p, net in facts.nets.items():
        if p in facts.xx_winners or net > tol:
            continue
        d = round((-net) - facts.recorded(p), 1)
        if d > tol:
            deficits[p] = d
        elif d < -tol:
            rep.repairs.append(Repair(
                player=p, kind=AMOUNT_OVERREAD, delta=-d,
                corroborators=["endpoint_net(C2)", "hand_conservation_direction(C1)"]))
            rep.notes.append(f"overread:{p}:{-d}")

    fixed = round(sum(deficits.values()), 1)
    residual = round(gap - fixed, 1)

    # ③ 残余 → 唯一赢家侧(C2′ 端点二次印证;rake 用容忍带,不硬给数)
    # 护栏(2026-06-12 单测猎出):存在【无端点读数的投入者】时禁用赢家侧兜底——
    # 缺口可能是他们的,吸到赢家头上=张冠李戴;宁可 UNSOLVED 让病因分类指 MAPPING_GAP。
    contributors = {p for (p, _st) in facts.street_contrib}
    has_unmapped_contributor = bool(contributors - set(facts.nets))
    winners = sorted(facts.xx_winners | {p for p, n in facts.nets.items() if n > tol})
    winner_fix = 0.0
    if residual > tol and len(winners) == 1 and not has_unmapped_contributor:
        w = winners[0]
        if w in facts.nets:
            # C2′:投入_w ≈ pot - rake - net_w;其缺口 = (pot - net_w - 投入_w(已记)) 减 rake∈[0,rake_allow]
            implied = round(facts.pot_final - facts.nets[w] - facts.recorded(w), 1)
            if abs(implied - residual) <= tol + rake_allow:
                winner_fix = residual
                deficits[w] = residual
                rep.notes.append(f"winner_side:{w}:{residual}(C2′印证,含rake带)")

    # ④ 产出修复(每条 2 印证:端点 × 手级守恒)
    for p, d in deficits.items():
        streets, uncertain = _assign_streets(facts, p, d, tol)
        rep.repairs.append(Repair(
            player=p, kind=MISSING_CONTRIB, delta=d, streets=streets,
            street_uncertain=uncertain,
            corroborators=["endpoint_net(C2/C2′)", "hand_conservation(C1)"]))

    rep.gap_after = round(gap - fixed - winner_fix, 1)
    # 残余落在 [-rake_allow, tol] = rake 可解释 → REPAIRED;否则 UNSOLVED(不硬凑)
    if -rake_allow <= rep.gap_after <= tol:
        rep.status = REPAIRED
    else:
        rep.status = UNSOLVED
        rep.notes.append(f"residual={rep.gap_after}")
    return rep
