"""👁 实时模块 — 当前桌动态画像.

周期 refresh(30-60s),显示当前桌每个 seat 的玩家 + 当前 stat 快照.
"""

import streamlit as st


def render():
    st.title("👁 实时 — 当前桌动态画像")

    st.info(
        "📌 实时模块占位.\n\n"
        "TODO:\n"
        "- 显示当前桌 8 seat 的玩家昵称\n"
        "- 每 seat 当前 stat 卡(VPIP / PFR / AF / Snap%)\n"
        "- Hand 进行时高亮当前行动玩家\n"
        "- 周期 refresh(30-60s,Streamlit `st.rerun` 或 placeholder)"
    )

    if st.button("↻ 刷新"):
        st.rerun()
