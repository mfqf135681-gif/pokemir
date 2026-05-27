"""📊 复盘模块 — 对手画像 / Hand 历史 / 图表.

数据来源:
- 对手画像表:v_player_vpip_pfr_af + v_player_3bet + v_player_net_winnings(view 待实施)
- Hand 历史:hands + action_events(已可用)

placeholder 期间显示 "等待 view 实施 — 用基础表数据兜底" 占位.
"""

import pandas as pd
import streamlit as st

from dashboard import stats


def render():
    st.title("📊 复盘 — 对手画像 + Hand 历史")

    # Top metrics
    col1, col2, col3 = st.columns(3)
    col1.metric("累计 Hand 数", f"{stats.get_total_hands():,}")
    col2.metric("累计 Action 事件", f"{stats.get_total_events():,}")
    col3.metric("Unique 玩家数", f"{stats.get_unique_players():,}")

    st.divider()

    # Tab 1: 对手画像
    tab_profile, tab_history = st.tabs(["对手画像", "Hand 历史"])

    with tab_profile:
        st.subheader("🎭 玩家画像(VPIP / PFR / AF)")
        players = stats.get_player_vpip_pfr_af(min_hands=5)
        if players:
            df = pd.DataFrame(players)
            st.dataframe(df, use_container_width=True)
        else:
            st.info(
                "📌 等待 `v_player_vpip_pfr_af` view 实施.\n\n"
                "View 字段契约见 `requirement-discussions/2026-05-27_03-01-00"
                "_异步开发准备_家里dashboard.md` §6."
            )
            # Fallback:用 action_events 简单聚合显示
            st.caption("Fallback:基础 action 统计")
            from dashboard.db import safe_query
            basic = safe_query(
                """
                SELECT player_name,
                       COUNT(*) AS n_events,
                       SUM(CASE WHEN action_type='fold' THEN 1 ELSE 0 END) AS n_fold,
                       SUM(CASE WHEN action_type IN ('bet','raise','all_in') THEN 1 ELSE 0 END) AS n_aggressive
                FROM action_events
                GROUP BY player_name
                HAVING COUNT(*) >= 5
                ORDER BY n_events DESC
                LIMIT 30
                """
            )
            if basic:
                st.dataframe(pd.DataFrame(basic), use_container_width=True)

    with tab_history:
        st.subheader("📜 最近 Hand 历史")
        hands = stats.get_recent_hands(limit=30)
        if hands:
            df = pd.DataFrame(hands)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("尚无数据 — 录制几局后回来查看.")
