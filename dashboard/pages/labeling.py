"""事件标注模块 — 最近 action_events 滚动查看 + 人工纠偏."""

from __future__ import annotations

from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import text

from dashboard.db import get_engine


ACTION_LABELS = {
    "fold": "弃牌",
    "check": "过牌",
    "call": "跟注",
    "bet": "下注",
    "raise": "加注",
    "all_in": "全押",
}
ACTION_OPTIONS = list(ACTION_LABELS.keys())


def _format_time(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S")
    return "--:--:--"


def _format_amount(value) -> str:
    if value is None:
        return ""
    if float(value).is_integer():
        return f" ${int(value)}"
    return f" ${value:.2f}"


def _format_confidence(value) -> str:
    if value is None:
        return "置信 --"
    return f"置信 {int(round(float(value) * 100))}%"


def _fetch_recent_events() -> tuple[list[dict], str | None]:
    sql = """
        SELECT
            id,
            player_name,
            position,
            action_type,
            amount,
            confidence_score,
            COALESCE(timestamp, created_at) AS event_time
        FROM action_events
        ORDER BY COALESCE(timestamp, created_at) DESC
        LIMIT 50
    """
    try:
        with get_engine().connect() as conn:
            result = conn.execute(text(sql))
            return [dict(row._mapping) for row in result], None
    except Exception as exc:
        return [], str(exc)[:200]


def _save_correction(event: dict, corrected_action: str, notes: str | None) -> tuple[bool, str]:
    sql = """
        INSERT INTO event_corrections (
            event_id,
            original_action,
            corrected_action,
            notes
        ) VALUES (
            :event_id,
            :original_action,
            :corrected_action,
            :notes
        )
    """
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text(sql),
                {
                    "event_id": event["id"],
                    "original_action": event["action_type"],
                    "corrected_action": corrected_action,
                    "notes": notes or None,
                },
            )
        return True, "纠偏已保存"
    except Exception as exc:
        return False, f"保存失败: {str(exc)[:160]}"


def _render_event(event: dict) -> None:
    action_label = ACTION_LABELS.get(event["action_type"], event["action_type"])
    line = (
        f"[{_format_time(event['event_time'])}] "
        f"{event['player_name']} ({event['position']}) "
        f"{action_label}{_format_amount(event['amount'])} "
        f"{_format_confidence(event['confidence_score'])}"
    )

    st.markdown(line)
    with st.expander("纠偏▼"):
        corrected_action = st.selectbox(
            "正确动作",
            ACTION_OPTIONS,
            format_func=lambda value: ACTION_LABELS[value],
            key=f"correction_action_{event['id']}",
        )
        notes = st.text_input("备注", key=f"correction_notes_{event['id']}")
        if st.button("保存纠偏", key=f"save_correction_{event['id']}"):
            ok, message = _save_correction(event, corrected_action, notes)
            if ok:
                st.success(message)
            else:
                st.error(message)


def render():
    st.title("事件标注")

    auto_refresh = st.toggle("每 5 秒自动刷新", value=True)
    if auto_refresh:
        components.html(
            """
            <script>
            setTimeout(function() {
                window.parent.location.reload();
            }, 5000);
            </script>
            """,
            height=0,
        )

    events, error = _fetch_recent_events()
    if error:
        st.warning(f"读取 action_events 失败: {error}")
        st.caption("如果刚更新代码但还没应用 schema,请先按本次 change-log 的手动步骤建表。")
        return

    st.caption(f"最近 {len(events)} 条 action_events")
    if not events:
        st.info("暂无事件。")
        return

    for event in events:
        _render_event(event)
        st.divider()