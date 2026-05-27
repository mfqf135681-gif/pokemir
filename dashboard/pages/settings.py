"""⚙ 设置模块 — Pipeline 控制 / ROI 配置查看 / DB 信息."""

import os

import streamlit as st

from dashboard.db import get_engine, safe_query


def render():
    st.title("⚙ 设置")

    tab_db, tab_roi, tab_about = st.tabs(["DB 信息", "ROI 配置", "关于"])

    with tab_db:
        st.subheader("DB 连接")
        dsn = os.getenv("POKEMIR_DB_DSN_SYNC", "(未配置 .env,使用默认 100.101.105.46)")
        # 隐藏密码
        masked = dsn
        if "@" in dsn and ":" in dsn:
            try:
                proto, rest = dsn.split("://", 1)
                userpass, host = rest.split("@", 1)
                user = userpass.split(":")[0]
                masked = f"{proto}://{user}:****@{host}"
            except Exception:
                pass
        st.code(masked)

        st.subheader("DB 状态")
        if st.button("Refresh 状态"):
            st.rerun()
        try:
            with get_engine().connect():
                st.success("✓ 连接 OK")
            stats_rows = safe_query("""
                SELECT 'hands' AS t, COUNT(*)::text AS n FROM hands
                UNION ALL SELECT 'action_events', COUNT(*)::text FROM action_events
                UNION ALL SELECT 'diagnostic_events', COUNT(*)::text FROM diagnostic_events
            """)
            import pandas as pd
            st.dataframe(pd.DataFrame(stats_rows), use_container_width=True)
        except Exception as e:
            st.error(f"连接失败: {e}")

    with tab_roi:
        st.subheader("ROI Profile")
        from pathlib import Path
        import json
        for profile in sorted(Path("rois").glob("*.json")) if Path("rois").exists() else []:
            with st.expander(profile.name):
                try:
                    d = json.loads(profile.read_text(encoding="utf-8"))
                    st.json(d)
                except Exception as e:
                    st.error(f"读取失败: {e}")

    with tab_about:
        from dashboard import __version__
        st.markdown(f"""
**Pokemir Dashboard** v{__version__}

WePoker H5 对手画像追踪 — 仅自用,**严格遵守 ToS 边界**.

- 后端 pipeline 在 Win 桌面运行(独立进程)
- 此 dashboard 仅读 DB,**不写**
- DB 在 VPS 私有部署,**Tailscale 内网访问**

详见 `requirement-discussions/` 各决策文档.
""")
