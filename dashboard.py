"""Pokemir Dashboard — Streamlit entry point.

启动:
    streamlit run dashboard.py

打开浏览器:http://localhost:8501

架构:本文件仅做 sidebar navigation + 状态显示,真正各模块在
dashboard/pages/ 下.SQL 读由 dashboard/db.py + dashboard/stats.py 封装.

依赖:requirements-dashboard.txt(不需要 PyTorch / EasyOCR 等 pipeline-only).
DB 连接:.env 的 POKEMIR_DB_DSN_SYNC,默认指向 VPS Tailnet 节点.

⚠️ Streamlit 浏览器 UI 行为我无法在 Linux VPS 验证(无浏览器).
   你 Win / 家里电脑端是第一真实验证者.若 UI 异常请告诉我具体页面 + 操作.
"""

from __future__ import annotations

import streamlit as st

from dashboard.db import db_health_check
from dashboard.pages import coach, live, replay, settings

st.set_page_config(
    page_title="Pokemir",
    page_icon="🃏",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGES = {
    "📊 复盘": replay.render,
    "👁 实时": live.render,
    "🤖 AI 教练": coach.render,
    "⚙ 设置": settings.render,
}


def main():
    with st.sidebar:
        st.markdown("## 🃏 Pokemir")
        st.caption("WePoker 对手画像追踪")
        st.markdown("---")
        page = st.radio(
            "模块",
            list(PAGES.keys()),
            label_visibility="collapsed",
        )
        st.markdown("---")
        st.markdown("### 状态")
        ok, msg = db_health_check()
        icon = "🟢" if ok else "🔴"
        st.caption(f"DB: {icon} {msg}")

    PAGES[page]()


if __name__ == "__main__":
    main()
