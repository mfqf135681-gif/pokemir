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
    tab_profile, tab_history, tab_solved = st.tabs(["对手画像", "Hand 历史", "逐手求解复盘"])

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
                WHERE player_name NOT LIKE 'TempUser_%'
                  -- T65(2026-05-29):排除 synthetic POST events,n_events 反映真 actions
                  AND NOT COALESCE((raw_data->>'synthetic')::bool, FALSE)
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

    with tab_solved:
        _render_solved_replay()


def _render_solved_replay():
    """🧮 逐手求解复盘(#226 砖4):选一手 → 现场求解 → 按街时间线。

    展示铁律(solver/replay_view 同源):推断行带 🔧 与原始记录双重区分,
    UNSOLVED 必须透出病因与残余 —— 绝不把推算的冒充看见的。"""
    from dashboard.db import safe_query
    from solver.diagnose import classify_unsolved
    from solver.hand_repair import UNSOLVED, solve_hand
    from solver.replay_view import build_solved_timeline
    from tools.solve_hands import build_facts
    from tools.solve_session import seat_player_map

    st.subheader("🧮 逐手求解复盘(识别记录 + 账房先生补账)")
    recent = safe_query(
        """SELECT h.id::text AS id, to_char(h.started_at,'MM-DD HH24:MI:SS') AS t,
                  h.pot_size_final AS pot
           FROM hands h WHERE h.ended_at IS NOT NULL
           ORDER BY h.started_at DESC LIMIT 60""")
    if not recent:
        st.info("尚无数据 — 录制几局后回来查看.")
        return
    label_of = {f"{r['t']}  pot={r['pot']}  {r['id'][:8]}": r["id"] for r in recent}
    choice = st.selectbox("选一手", list(label_of.keys()))
    hid = label_of[choice]

    hrow = safe_query(
        """SELECT h.pot_size_final AS pot,
                  h.raw_data->'player_stacks_initial' AS init,
                  h.raw_data->'player_stacks_final'  AS fin,
                  h.result->'win_amounts_xx' AS xx,
                  h.seats AS seats, h.raw_data->>'sb_seat' AS sb_seat,
                  h.raw_data->>'bb_seat' AS bb_seat,
                  h.raw_data->>'button_seat_index' AS btn_seat,
                  h.raw_data->'insurance_inferred' AS insurance
           FROM hands h WHERE h.id = :hid""", {"hid": hid})
    erows = safe_query(
        """SELECT sequence_number AS seq, street, player_name AS player,
                  action_type AS action, amount,
                  (raw_data->>'stack_before')::float AS stk_b
           FROM action_events WHERE hand_id = :hid ORDER BY sequence_number""",
        {"hid": hid})
    if not hrow:
        st.warning("该手数据不完整.")
        return
    h = {**hrow[0], "id": hid,
         "init": hrow[0]["init"] or {}, "fin": hrow[0]["fin"] or {},
         "xx": hrow[0]["xx"] or {}, "seats": hrow[0]["seats"] or {},
         "insurance": hrow[0]["insurance"] or []}
    ante_pairs = [(e["player"], e["stk_b"]) for e in (erows or [])
                  if e["action"] == "post_ante"]
    ev_tuples = [(e["player"], e["street"], e["action"], e["amount"], e["stk_b"])
                 for e in (erows or [])]
    sm = seat_player_map(h, {hid: ante_pairs})
    facts = build_facts(h, ev_tuples, sm)
    report = solve_hand(facts)
    cause = None
    if report.status == UNSOLVED:
        cause = classify_unsolved(
            facts, report,
            has_allin=any(e["action"] == "all_in" for e in (erows or [])),
            has_insurance_hint=bool(h["insurance"]))
    tl = build_solved_timeline(facts, report, erows or [], cause=cause)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("状态", tl["badge"])
    c2.metric("底池", tl["pot_final"])
    c3.metric("原始缺口", tl["gap_before"])
    c4.metric("求解后残余", tl["residual"])
    if tl["status"] == "UNSOLVED":
        st.warning(f"病因: {tl.get('cause')} — {tl.get('cause_evidence')}")
    elif tl["repairs_n"]:
        st.success(f"补账 {tl['repairs_n']} 笔(下方带 🔧 的行;原始记录原样保留)")

    for s in tl["streets"]:
        st.markdown(f"**{s['street']}**")
        st.dataframe(pd.DataFrame(s["rows"]), use_container_width=True, hide_index=True)

    st.markdown("**资金小结(端点口径)**")
    st.dataframe(pd.DataFrame(tl["summary"]), use_container_width=True, hide_index=True)
