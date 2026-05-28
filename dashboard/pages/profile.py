"""👤 对手画像 — T19 位置维度多维画像(2026-05-28).

读 v_player_position_matrix + v_player_net_winnings + action_events,
显示每玩家:整体 stat + 位置矩阵 + 净胜负 + 类型判定。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.db import safe_query

POSITION_ORDER = ["UTG", "UTG+1", "MP", "HJ", "CO", "BTN", "SB", "BB"]

ACTION_CN = {
    "fold": "弃牌", "check": "过牌", "call": "跟注",
    "bet": "下注", "raise": "加注", "all_in": "全押",
}


def _classify(vpip, pfr, af) -> tuple[str, str]:
    """返回 (类型 emoji+标签, 通俗描述)。"""
    if vpip is None:
        return "❓", "数据不足"
    af_v = float(af) if af is not None else 0
    pfr_v = pfr if pfr is not None else 0
    if vpip >= 70 and af_v < 1:
        return "🐟 大鱼", "啥都跟,几乎不主动加,**狠 value**"
    if vpip >= 70 and af_v >= 1:
        return "🐗 Maniac", "啥都玩还猛加,**等好牌 trap**"
    if vpip >= 40 and pfr_v >= 25 and af_v > 1.5:
        return "🦈 LAG 真凶", "范围广 + 主动施压,**惹不起,避开**"
    if vpip >= 40 and af_v < 1:
        return "🐠 跟池站", "中等水货,**有牌就 value**"
    if 20 <= vpip <= 35 and pfr_v >= 15 and af_v >= 1:
        return "🦅 TAG", "**职业型** — 范围紧 + 主动激进"
    if vpip < 20:
        return "🪨 Nit/Rock", "极紧,**他 raise = 真品强牌,直接弃**"
    return "🤷 混合型", "stat 不极端,综合判断"


def _calc_overall_stats(player: str):
    rows = safe_query(
        """
        WITH ph AS (SELECT DISTINCT hand_id FROM action_events WHERE player_name = %s),
        vpip AS (SELECT DISTINCT hand_id FROM action_events WHERE player_name = %s
                 AND street='preflop' AND action_type IN ('call','bet','raise','all_in')),
        pfr AS (SELECT DISTINCT hand_id FROM action_events WHERE player_name = %s
                AND street='preflop' AND action_type IN ('bet','raise','all_in')),
        agg AS (SELECT
                  SUM(CASE WHEN action_type IN ('bet','raise') THEN 1 ELSE 0 END) AS aggr,
                  SUM(CASE WHEN action_type='call' THEN 1 ELSE 0 END) AS passive
                FROM action_events WHERE player_name = %s)
        SELECT
          (SELECT COUNT(*) FROM ph) AS hands,
          ROUND(100.0*(SELECT COUNT(*) FROM vpip)/NULLIF((SELECT COUNT(*) FROM ph),0),0)::int AS vpip,
          ROUND(100.0*(SELECT COUNT(*) FROM pfr)/NULLIF((SELECT COUNT(*) FROM ph),0),0)::int AS pfr,
          ROUND((agg.aggr::numeric/NULLIF(agg.passive,0))::numeric, 2) AS af
        FROM agg
        """,
        (player, player, player, player),
    )
    return rows[0] if rows else None


def render():
    st.title("👤 对手画像 — 位置维度")
    st.caption("找谁打得紧 / 谁打得乱,凭数据下决断。**样本 < 10 手数字仅供参考**.")

    players = safe_query(
        """
        SELECT player_name, COUNT(DISTINCT hand_id) AS hands
        FROM action_events
        WHERE player_name IS NOT NULL AND player_name NOT LIKE 'TempUser%%'
        GROUP BY player_name
        HAVING COUNT(DISTINCT hand_id) >= 10
        ORDER BY hands DESC
        """
    )
    if not players:
        st.info("📊 数据不足:还没有玩家累计到 10 手以上,继续录数据。")
        return

    options = [f"{p['player_name']} ({p['hands']} 手)" for p in players]
    sel = st.selectbox("选玩家:", options, index=0)
    player_name = sel.split(" (")[0]

    stats = _calc_overall_stats(player_name)
    if not stats:
        st.warning("此玩家没有完整 stat 数据。")
        return

    label, desc = _classify(stats.get("vpip"), stats.get("pfr"), stats.get("af"))

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("总手数", stats["hands"])
    c2.metric("入池率 VPIP", f"{stats['vpip']}%" if stats["vpip"] is not None else "—")
    c3.metric("主动加注 PFR", f"{stats['pfr']}%" if stats["pfr"] is not None else "—")
    c4.metric("攻击系数 AF", f"{stats['af']}" if stats["af"] is not None else "—")
    c5.metric("类型", label)
    st.success(f"**对抗建议**:{desc}")

    st.divider()

    st.subheader("🗺️ 位置维度画像")
    matrix = safe_query(
        "SELECT position, hands, vpip_pct, pfr_pct FROM v_player_position_matrix WHERE player_name = %s",
        (player_name,),
    )
    if matrix:
        df = pd.DataFrame(matrix)
        df["position"] = pd.Categorical(df["position"], categories=POSITION_ORDER, ordered=True)
        df = df.sort_values("position").rename(columns={
            "position": "位置", "hands": "手数",
            "vpip_pct": "VPIP%", "pfr_pct": "PFR%",
        })
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption("⚠️ 每位置手数 < 5 时数字噪声大;**手数越多越可信**。")
    else:
        st.info("位置数据不足。")

    st.divider()

    st.subheader("💰 净胜负 trend(近似,不含 rake)")
    winnings = safe_query(
        "SELECT hands_traced, rebuy_count, rebuy_total, net_excl_rebuy, net_naive, min_stack, max_stack "
        "FROM v_player_net_winnings WHERE 玩家 = %s",
        (player_name,),
    )
    if winnings:
        w = winnings[0]
        net = w["net_excl_rebuy"] or 0
        sign = "📈" if net > 0 else ("📉" if net < 0 else "➖")
        c1, c2, c3 = st.columns(3)
        c1.metric(f"净胜负 {sign}", f"{net:+.0f}")
        c2.metric("rebuy 次", w["rebuy_count"])
        c3.metric("stack 区间", f"{w['min_stack']:.0f} - {w['max_stack']:.0f}")
        st.caption("⚠️ trend only,不含 rake,精确数字等 #LR4 完成。")
    else:
        st.info("净胜负数据不足。")

    st.divider()

    st.subheader("🎬 各 street 行为")
    streets = safe_query(
        """
        SELECT street, action_type, COUNT(*) AS n
        FROM action_events WHERE player_name = %s
        GROUP BY street, action_type
        ORDER BY CASE street WHEN 'preflop' THEN 1 WHEN 'flop' THEN 2
                             WHEN 'turn' THEN 3 WHEN 'river' THEN 4 END
        """,
        (player_name,),
    )
    if streets:
        sdf = pd.DataFrame(streets)
        sdf["action_cn"] = sdf["action_type"].map(ACTION_CN).fillna(sdf["action_type"])
        pivot = sdf.pivot_table(
            index="street", columns="action_cn", values="n", fill_value=0
        ).reindex(["preflop", "flop", "turn", "river"])
        st.dataframe(pivot, use_container_width=True)
    else:
        st.info("行动数据不足。")
