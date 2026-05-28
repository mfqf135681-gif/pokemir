"""DB connection + health check for dashboard.

默认读 hands / action_events / diagnostic_events + 各 v_* views.
事件标注与 Pipeline 设置页只写独立辅助表,不改 action_events.raw_data.
"""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError

_engine: Optional[Engine] = None


def get_engine() -> Engine:
    """返回 SQLAlchemy engine 单例.从 .env 的 POKEMIR_DB_DSN_SYNC 读连接串,
    默认指向 VPS Tailnet 节点(100.101.105.46).
    """
    global _engine
    if _engine is None:
        dsn = os.getenv(
            "POKEMIR_DB_DSN_SYNC",
            "postgresql://poker_user:poker_pass@100.101.105.46:5432/poker_assistant",
        )
        _engine = create_engine(
            dsn,
            pool_pre_ping=True,   # 连接复用前 ping,Tailscale 网络抖动友好
            pool_size=2,
            pool_recycle=1800,    # 30 min 自动重连
            connect_args={"connect_timeout": 10},  # 10s 超时,Tailscale 跨网友好
        )
    return _engine


def db_health_check() -> tuple[bool, str]:
    """快速 DB 健康 check,sidebar 显示用.

    Returns (ok, message) — 失败返 (False, "原因 truncated to 50 chars").
    """
    try:
        with get_engine().connect() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM hands")).scalar()
        return True, f"{n} hands"
    except OperationalError as e:
        return False, f"连接失败: {str(e)[:50]}"
    except Exception as e:
        return False, f"错误: {str(e)[:50]}"


def safe_query(sql: str, params: dict | None = None) -> list[dict]:
    """执行 SELECT,返 list of dict.失败返 [].

    用于 stats.py 的所有查询的兜底封装 — view 未实施时不让 dashboard 崩.
    """
    try:
        with get_engine().connect() as conn:
            result = conn.execute(text(sql), params or {})
            return [dict(row._mapping) for row in result]
    except Exception:
        return []
