"""solver/diagnose.py — 砖2:UNSOLVED 病因分类 + pot 反审(圈梁 J-7 三角自检)。

砖1 输出"补不平"后,本模块回答"为什么不平"。每个病因 = 一个可检验的签名,
带证据串(纪律5 追溯);分类只依据原始约束(纪律2 深度=1),分错也只影响
排查顺序,不影响任何数据。

病因(按检验顺序,首中即归):
  POT_SUSPECT_LOW   端点说输家们实际输掉的钱 > pot+rake容忍 → pot 读小了
                    (钱物理上离开了输家的栈,pot 却装不下 → J-7 三角指向 pot)
  RECORD_OVERREAD   已记录投入 > pot 且 > 端点能支持的量 → 某笔金额记大了(-2467 型)
  INSURANCE_SUSPECT 残余为正小额 且 本手有 all-in/保险推断 → 保费/赔付走了池外
  MAPPING_GAP       有投入记录的玩家缺端点(座位映射缺口)→ 缺口找不到主
  SMALL_RESIDUAL    |残余| ≤ 小额带(默认 3bb)→ 手末快照时序/ante 噪声级
  UNKNOWN           以上都不是 → 真正的新东西,优先人工看
"""
from __future__ import annotations

POT_SUSPECT_LOW = "POT_SUSPECT_LOW"
RECORD_OVERREAD = "RECORD_OVERREAD"
INSURANCE_SUSPECT = "INSURANCE_SUSPECT"
MAPPING_GAP = "MAPPING_GAP"
SMALL_RESIDUAL = "SMALL_RESIDUAL"
UNKNOWN = "UNKNOWN"


def classify_unsolved(facts, report, has_allin: bool = False,
                      has_insurance_hint: bool = False,
                      tol: float = 6.0, rake_allow: float = 30.0,
                      small_band: float = 12.0) -> dict:
    """facts: HandFacts;report: RepairReport(status=UNSOLVED)。返回 {cause, evidence}。"""
    residual = report.gap_after if report.gap_after is not None else report.gap_before
    pot = facts.pot_final or 0.0
    recorded = facts.recorded_total()
    losses = sum(-n for p, n in facts.nets.items()
                 if n < -tol and p not in facts.xx_winners)

    # J-7 三角①:输家栈里实际流出的钱 pot 装不下 → pot 读小了
    if losses > pot + rake_allow + tol:
        return {"cause": POT_SUSPECT_LOW,
                "evidence": f"losses_by_endpoint={losses:.0f} > pot={pot:.0f}+rake带"}

    # J-7 三角②:记录超 pot 又超端点支持 → 记录侧有大额超读
    if residual is not None and residual < -(rake_allow + tol):
        return {"cause": RECORD_OVERREAD,
                "evidence": f"recorded={recorded:.0f} 超 pot={pot:.0f} 达 {-residual:.0f}"}

    # 投入有记录但端点缺读的玩家 → 缺口可能就是他们的
    contributors = {p for (p, _st) in facts.street_contrib}
    no_endpoint = sorted(contributors - set(facts.nets))
    if residual is not None and residual > tol and no_endpoint:
        return {"cause": MAPPING_GAP,
                "evidence": f"缺端点的投入者={no_endpoint}"}

    if residual is not None and 0 < residual <= small_band * 4 and \
            (has_allin or has_insurance_hint):
        return {"cause": INSURANCE_SUSPECT,
                "evidence": f"residual={residual} 且 allin={has_allin} ins_hint={has_insurance_hint}"}

    if residual is not None and abs(residual) <= small_band:
        return {"cause": SMALL_RESIDUAL, "evidence": f"residual={residual}"}

    return {"cause": UNKNOWN, "evidence": f"residual={residual}"}
