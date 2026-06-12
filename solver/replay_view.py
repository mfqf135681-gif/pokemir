"""solver/replay_view.py — 逐手求解复盘的时间线组装(纯逻辑,UI 无关)。

输入:该手的原始事件行 + HandFacts + RepairReport;
输出:渲染就绪的 dict(状态徽章 / 按街时间线 / 资金小结)。

铁律(圈梁纪律 4/5 的展示层延伸):
  - 推断行(修补)与原始记录【视觉与数据双重区分】:source="🔧推断",带印证列;
  - 绝不静默合并:UNSOLVED 手必须展示病因与残余,不假装完整;
  - 原始事件原样展示(含可疑行),修补只是【追加的解释】,不覆盖。
"""
from __future__ import annotations

from solver.hand_repair import EXACT, REPAIRED, UNSOLVED, HandFacts, RepairReport

STREETS = ["preflop", "flop", "turn", "river"]
_BADGE = {EXACT: "✅ 原账全对", REPAIRED: "🔧 已补账", UNSOLVED: "⚠️ 补不平"}


def build_solved_timeline(facts: HandFacts, report: RepairReport,
                          event_rows: list[dict], cause: dict | None = None) -> dict:
    """event_rows: [{seq, street, player, action, amount}](原始顺序)。
    返回 {status, badge, streets: [{street, rows}], summary, repairs_n, residual}。"""
    # 按街分桶,原始行打 source="记录"
    by_street: dict[str, list[dict]] = {st: [] for st in STREETS}
    for e in event_rows:
        st = e.get("street") or "preflop"
        if st not in by_street:
            by_street[st] = []
        by_street[st].append({
            "seq": e.get("seq"), "player": e.get("player"),
            "action": e.get("action"), "amount": e.get("amount"),
            "source": "记录", "note": "",
        })

    # 修补行追加到对应街(街不定 → 标注)
    for rep in report.repairs:
        targets = rep.streets or [(None, rep.delta)]
        for st, amt in targets:
            row = {
                "seq": None, "player": rep.player,
                "action": "补投入" if rep.kind == "MISSING_CONTRIB" else "金额存疑",
                "amount": amt, "source": "🔧推断",
                "note": ("街不定; " if rep.street_uncertain else "")
                        + "印证: " + "+".join(rep.corroborators),
            }
            bucket = by_street.get(st or "river")
            (bucket if bucket is not None else by_street["river"]).append(row)

    streets = [{"street": st, "rows": rows}
               for st, rows in by_street.items() if rows]

    # 资金小结(端点口径)
    summary = []
    for p in sorted(set(facts.nets) | {pl for (pl, _s) in facts.street_contrib}):
        summary.append({
            "player": p,
            "净额(端点)": facts.nets.get(p),
            "已记录投入": round(facts.recorded(p), 1),
            "+xx赢家": "✓" if p in facts.xx_winners else "",
        })

    out = {
        "status": report.status,
        "badge": _BADGE.get(report.status, report.status),
        "streets": streets,
        "summary": summary,
        "repairs_n": len(report.repairs),
        "gap_before": report.gap_before,
        "residual": report.gap_after,
        "pot_final": facts.pot_final,
    }
    if report.status == UNSOLVED:
        out["cause"] = (cause or {}).get("cause", "UNKNOWN")
        out["cause_evidence"] = (cause or {}).get("evidence", "")
    return out
