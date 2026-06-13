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


# 病因 → 中文人话(UNSOLVED 筛选/标签用)
_CAUSE_CN = {
    "FINAL_SNAPSHOT_SUSPECT": "终栈被摊牌动画拍坏",
    "POT_SUSPECT_LOW": "底池读偏小",
    "RECORD_OVERREAD": "某笔金额读偏大",
    "INSURANCE_SUSPECT": "疑似保险走池外",
    "MAPPING_GAP": "座位对不上号",
    "SMALL_RESIDUAL": "小额残余(快照时序)",
    "UNKNOWN": "未归类(待人工看)",
}
_STATUS_ICON = {"EXACT": "✅", "REPAIRED": "🔧", "UNSOLVED": "⚠️"}


@st.cache_data(ttl=60, show_spinner="求解最近牌局…")
def _solve_recent(limit: int) -> list[dict]:
    """批量求解最近 limit 手(整 session 链愈合 + 逐手补账),结果缓存 60s。

    与 tools/solve_hands 同一套纯函数(单一实现,防漂移):seat_player_map →
    heal_finals(链愈合)→ build_facts → solve_hand → classify_unsolved。
    返回每手 {hid,t,pot,status,cause,cause_cn,repairs_n,gap_before,residual}。"""
    from collections import defaultdict

    from dashboard.db import safe_query
    from solver.diagnose import classify_unsolved
    from solver.endpoint_chain import HandPoint, heal_finals
    from solver.hand_repair import UNSOLVED, solve_hand
    from solver.replay_view import build_solved_timeline
    from tools.solve_hands import build_facts
    from tools.solve_session import seat_player_map

    # 最近 limit 手(子查询复用,避免给 text() 传 list→PG array 不展开 + uuid/text 不匹配的坑)
    _recent_sql = ("SELECT id FROM hands WHERE ended_at IS NOT NULL "
                   "ORDER BY started_at DESC LIMIT :lim")
    hands = safe_query(
        f"""SELECT h.id::text AS id, to_char(h.started_at,'MM-DD HH24:MI:SS') AS t,
                  h.pot_size_final AS pot,
                  h.raw_data->'player_stacks_initial' AS init,
                  h.raw_data->'player_stacks_final'  AS fin,
                  h.result->'win_amounts_xx' AS xx, h.seats AS seats,
                  h.raw_data->>'sb_seat' AS sb_seat, h.raw_data->>'bb_seat' AS bb_seat,
                  h.raw_data->>'button_seat_index' AS btn_seat,
                  h.raw_data->'insurance_inferred' AS insurance
           FROM hands h WHERE h.id IN ({_recent_sql})
           ORDER BY h.started_at""", {"lim": limit})   # 直接升序(链愈合需手序)
    if not hands:
        return []
    ev = safe_query(
        f"""SELECT ae.hand_id::text AS hid, ae.sequence_number AS seq, ae.street,
                  ae.player_name AS player, ae.action_type AS action, ae.amount,
                  (ae.raw_data->>'stack_before')::float AS stk_b
           FROM action_events ae
           WHERE ae.hand_id IN ({_recent_sql})
           ORDER BY ae.hand_id, ae.sequence_number""", {"lim": limit})
    by_hand_ev = defaultdict(list)
    for e in (ev or []):
        by_hand_ev[e["hid"]].append(e)

    for h in hands:
        h["init"] = h["init"] or {}
        h["fin"] = h["fin"] or {}
        h["xx"] = h["xx"] or {}
        h["seats"] = h["seats"] or {}
        h["insurance"] = h["insurance"] or []

    seat_maps, by_player = {}, defaultdict(list)
    for idx, h in enumerate(hands):
        ante_pairs = [(e["player"], e["stk_b"]) for e in by_hand_ev[h["id"]]
                      if e["action"] == "post_ante"]
        sm = seat_player_map(h, {h["id"]: ante_pairs})
        seat_maps[h["id"]] = sm
        for seat, pl in sm.items():
            i, f = h["init"].get(seat), h["fin"].get(seat)
            by_player[pl].append(HandPoint(
                hand_id=h["id"], idx=idx, player=pl,
                initial=float(i) if i is not None else None,
                final=float(f) if f is not None else None))
    _, heal_recs = heal_finals(by_player, {h["id"]: h["pot"] for h in hands})
    overrides = defaultdict(dict)
    for r in heal_recs:
        overrides[r["hand"]][r["player"]] = r["healed_final"]

    out = []
    for h in hands:
        evs = by_hand_ev[h["id"]]
        ev_tuples = [(e["player"], e["street"], e["action"], e["amount"], e["stk_b"]) for e in evs]
        facts = build_facts(h, ev_tuples, seat_maps[h["id"]], final_override=overrides.get(h["id"]))
        rep = solve_hand(facts)
        cause = None
        if rep.status == UNSOLVED:
            cause = classify_unsolved(facts, rep,
                                      has_allin=any(e["action"] == "all_in" for e in evs),
                                      has_insurance_hint=bool(h["insurance"]))
        # 复盘审计修(2026-06-12):存完整 tl,详情页直接渲染缓存结果 —— 列表(愈合后)与
        # 详情【同一次求解】,杜绝"列表标已补账、详情显补不平"的状态打架(单手重算无链=无愈合)。
        tl = build_solved_timeline(facts, rep, evs, cause=cause)
        out.append({"hid": h["id"], "t": h["t"], "pot": h["pot"], "status": rep.status,
                    "cause": (cause or {}).get("cause"),
                    "cause_cn": _CAUSE_CN.get((cause or {}).get("cause"), ""),
                    "repairs_n": len(rep.repairs), "gap_before": rep.gap_before,
                    "residual": rep.gap_after, "tl": tl})
    return list(reversed(out))   # 回到时间降序(最近在前)


def _render_solved_replay():
    """🧮 逐手求解复盘:批量求解(缓存)→ 按状态筛选(精准定位补不平)→ 单手时间线。

    展示铁律(solver/replay_view 同源):推断行带 🔧 与原始记录双重区分,
    UNSOLVED 必须透出病因与残余 —— 绝不把推算的冒充看见的。
    求解全在 _solve_recent(缓存,含链愈合);本函数只筛选+渲染缓存 tl。"""
    st.subheader("🧮 逐手求解复盘 — 识别记录 + 账房先生补账")

    col_n, col_f = st.columns([1, 2])
    limit = col_n.selectbox("范围(最近N手)", [30, 60, 120, 240], index=1)
    solved = _solve_recent(limit)
    if not solved:
        st.info("尚无数据 — 录制几局后回来查看。")
        return

    # 顶部:本批求解概览(分层 KPI)
    n = len(solved)
    n_exact = sum(1 for r in solved if r["status"] == "EXACT")
    n_rep = sum(1 for r in solved if r["status"] == "REPAIRED")
    n_uns = sum(1 for r in solved if r["status"] == "UNSOLVED")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("手数", n)
    k2.metric("✅ 原账全对", n_exact)
    k3.metric("🔧 已补账", n_rep)
    k4.metric("⚠️ 补不平", n_uns)

    # 筛选(核心:精准定位"补不平")
    filt = col_f.radio("筛选", ["全部", "⚠️ 只看补不平", "🔧 只看已补账", "✅ 只看原账全对"],
                       horizontal=True)
    _want = {"⚠️ 只看补不平": "UNSOLVED", "🔧 只看已补账": "REPAIRED",
             "✅ 只看原账全对": "EXACT"}.get(filt)
    shown = [r for r in solved if (_want is None or r["status"] == _want)]
    if not shown:
        st.info(f"本批最近 {n} 手没有「{filt}」的对局。")
        return

    def _label(r):
        ic = _STATUS_ICON.get(r["status"], "")
        tail = f" — {r['cause_cn']}" if r["status"] == "UNSOLVED" and r["cause_cn"] else ""
        return f"{ic} {r['t']}  pot={r['pot']}  {r['hid'][:8]}{tail}"

    label_map = {_label(r): r for r in shown}
    choice = st.selectbox(f"选一手(共 {len(shown)} 手)", list(label_map.keys()))
    tl = label_map[choice]["tl"]   # 直接取批量求解(含愈合)缓存的 tl,列表/详情同源一致

    # 状态横幅(配色:绿/蓝/橙)
    if tl["status"] == "EXACT":
        st.success("✅ 原账全对 — 识别记录自洽,无需补账")
    elif tl["status"] == "REPAIRED":
        st.info(f"🔧 已补账 {tl['repairs_n']} 笔 — 下方带 🔧 的行为账房先生推断(原始记录原样保留)")
    else:
        cn = _CAUSE_CN.get(tl.get("cause"), tl.get("cause", ""))
        st.warning(f"⚠️ 补不平 — 病因:{cn}（{tl.get('cause_evidence', '')}）")

    c1, c2, c3 = st.columns(3)
    c1.metric("底池", tl["pot_final"])
    c2.metric("原始缺口", tl["gap_before"])
    c3.metric("求解后残余", tl["residual"])

    # 按街时间线:推断行高亮(Styler 背景色,推断/记录视觉双重区分)
    def _hl(row):
        is_inf = row.get("source") == "🔧推断"
        return ["background-color: #fff3cd" if is_inf else "" for _ in row]

    for s in tl["streets"]:
        st.markdown(f"**{s['street']}**")
        df = pd.DataFrame(s["rows"]).rename(columns={
            "seq": "序", "player": "玩家", "action": "动作", "amount": "金额",
            "source": "来源", "note": "依据"})
        st.dataframe(df.style.apply(_hl, axis=1), use_container_width=True, hide_index=True)

    st.markdown("**资金小结(端点口径)**")
    sdf = pd.DataFrame(tl["summary"])
    st.dataframe(sdf, use_container_width=True, hide_index=True)
