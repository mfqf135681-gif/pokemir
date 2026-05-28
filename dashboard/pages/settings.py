"""⚙ 设置模块 — Pipeline 控制 / ROI 配置查看 / DB 信息."""

import os

import streamlit as st
from sqlalchemy import text

from dashboard.db import get_engine, safe_query


PROFILE_MODES = {
    "8 人坐下": ("party_poker_8", False),
    "8 人观战": ("party_poker_8", True),
    "9 人坐下": ("party_poker_9", False),
    "9 人观战": ("party_poker_9", True),
}


def _get_pipeline_setting() -> tuple[dict | None, str | None]:
    try:
        with get_engine().connect() as conn:
            row = conn.execute(text("""
                SELECT active_profile, observer_mode, updated_at
                FROM pipeline_settings
                ORDER BY id
                LIMIT 1
            """)).mappings().first()
            return dict(row) if row else None, None
    except Exception as exc:
        return None, str(exc)[:200]


def _set_pipeline_setting(active_profile: str, observer_mode: bool) -> tuple[bool, str]:
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pipeline_settings (id, active_profile, observer_mode, updated_at)
                    VALUES (1, :active_profile, :observer_mode, now())
                    ON CONFLICT (id) DO UPDATE SET
                        active_profile = EXCLUDED.active_profile,
                        observer_mode = EXCLUDED.observer_mode,
                        updated_at = now()
                """),
                {
                    "active_profile": active_profile,
                    "observer_mode": observer_mode,
                },
            )
        return True, "Pipeline 设置已保存"
    except Exception as exc:
        return False, f"保存失败: {str(exc)[:160]}"


def render():
    st.title("⚙ 设置")

    tab_db, tab_pipeline, tab_roi, tab_about = st.tabs(["DB 信息", "Pipeline", "ROI 配置", "关于"])

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

    with tab_pipeline:
        st.subheader("4 模式 ROI 切换")
        setting, error = _get_pipeline_setting()
        if error:
            st.warning(f"读取 pipeline_settings 失败: {error}")
            st.caption("如果刚更新代码但还没应用 schema,请先按本次 change-log 的手动步骤建表。")
        elif setting:
            mode_label = "观战" if setting["observer_mode"] else "坐下"
            st.info(f"当前 active_profile: {setting['active_profile']} ({mode_label})")
        else:
            st.info("当前 active_profile: 未初始化")

        cols = st.columns(4)
        for index, (label, (profile, observer_mode)) in enumerate(PROFILE_MODES.items()):
            with cols[index]:
                if st.button(label, use_container_width=True):
                    ok, message = _set_pipeline_setting(profile, observer_mode)
                    if ok:
                        st.success(message)
                        st.rerun()
                    else:
                        st.error(message)

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
- 此 dashboard 只向纠偏/设置辅助表写入,**不改 action_events 原始数据**
- DB 在 VPS 私有部署,**Tailscale 内网访问**

详见 `requirement-discussions/` 各决策文档.
""")
