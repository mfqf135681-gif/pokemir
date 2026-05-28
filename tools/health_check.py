"""T28 Pipeline 健康自检(2026-05-28 立)

防止长跑暗挂:每 30 min cron 跑一次 5 个 sanity SQL,异常时 emit
diagnostic_events level=WARN。用户长跑录数据早晨醒来看 diag 一眼知道。

用法:
    手动: python tools/health_check.py
    cron(VPS): */30 * * * * cd /home/alxe/project/pokemir && python tools/health_check.py >> /tmp/pokemir_health.log 2>&1

5 个 sanity 检查:
    1. 近 30 min hands 数 < 5      → pipeline 可能挂了 / 没玩牌
    2. avg_conf 近 30 min vs 历史 > 10% 下降 → OCR 退化
    3. override 率近 30 min > 10%   → P3 bug 可能复发
    4. WARN/ERROR diag 近 30 min > 20 → 异常累积
    5. stack_delta NULL 比例 > 50% → stack OCR 退化
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2
import psycopg2.extras


WINDOW_MINUTES = 30
WARNINGS_TO_EMIT: list[tuple[str, str, dict]] = []  # (tag, level, payload)


def _connect():
    dsn = os.getenv(
        "POKEMIR_DB_DSN_SYNC",
        f"postgresql://poker_user:{os.getenv('POKEMIR_DB_PASSWORD', 'change-me')}@localhost:5432/poker_assistant",
    )
    return psycopg2.connect(dsn)


def check_hands_rate(cur) -> None:
    cur.execute(f"""
        SELECT COUNT(*) FROM hands
        WHERE started_at > NOW() - INTERVAL '{WINDOW_MINUTES} minutes'
    """)
    n = cur.fetchone()[0]
    print(f"  [1] 近 {WINDOW_MINUTES} min hands: {n}")
    if n < 5:
        WARNINGS_TO_EMIT.append((
            "health.low_hand_rate",
            "WARN",
            {"hands_last_30min": n, "threshold": 5,
             "guess": "pipeline 挂了 / WePoker 桌空 / 录制结束未通知"},
        ))


def check_avg_conf_dropped(cur) -> None:
    cur.execute(f"""
        WITH recent AS (
          SELECT AVG(confidence_score) AS avg_recent
          FROM action_events ae JOIN hands h ON h.id = ae.hand_id
          WHERE h.started_at > NOW() - INTERVAL '{WINDOW_MINUTES} minutes'
        ),
        historic AS (
          SELECT AVG(confidence_score) AS avg_hist
          FROM action_events ae JOIN hands h ON h.id = ae.hand_id
          WHERE h.started_at < NOW() - INTERVAL '{WINDOW_MINUTES} minutes'
        )
        SELECT recent.avg_recent, historic.avg_hist FROM recent, historic
    """)
    row = cur.fetchone()
    if row is None or row[0] is None or row[1] is None:
        print(f"  [2] avg_conf: 数据不足跳过")
        return
    recent, hist = float(row[0]), float(row[1])
    drop_pct = (hist - recent) / hist * 100 if hist > 0 else 0
    print(f"  [2] avg_conf 近期 {recent:.3f} vs 历史 {hist:.3f} (drop {drop_pct:.1f}%)")
    if drop_pct > 10:
        WARNINGS_TO_EMIT.append((
            "health.conf_dropped",
            "WARN",
            {"recent_avg_conf": round(recent, 3),
             "historic_avg_conf": round(hist, 3),
             "drop_pct": round(drop_pct, 1),
             "guess": "OCR 退化 / 桌型变化 / WePoker UI 改"},
        ))


def check_override_rate(cur) -> None:
    cur.execute(f"""
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE ae.raw_data->>'override_reason' IS NOT NULL) AS overridden
        FROM action_events ae JOIN hands h ON h.id = ae.hand_id
        WHERE h.started_at > NOW() - INTERVAL '{WINDOW_MINUTES} minutes'
    """)
    total, ov = cur.fetchone()
    rate = (ov / total * 100) if total > 0 else 0
    print(f"  [3] override 率近 {WINDOW_MINUTES} min: {ov}/{total} = {rate:.1f}%")
    if total > 10 and rate > 10:
        WARNINGS_TO_EMIT.append((
            "health.override_spike",
            "WARN",
            {"recent_override_count": ov, "recent_total": total,
             "rate_pct": round(rate, 1),
             "guess": "P3 stack 物理校验异常触发频繁,可能 OCR bug / 桌型变化"},
        ))


def check_diag_warn_burst(cur) -> None:
    cur.execute(f"""
        SELECT COUNT(*) FROM diagnostic_events
        WHERE occurred_at > NOW() - INTERVAL '{WINDOW_MINUTES} minutes'
          AND level IN ('WARN', 'ERROR')
    """)
    n = cur.fetchone()[0]
    print(f"  [4] WARN/ERROR diag 近 {WINDOW_MINUTES} min: {n}")
    if n > 20:
        WARNINGS_TO_EMIT.append((
            "health.diag_burst",
            "WARN",
            {"warn_error_count": n, "threshold": 20,
             "guess": "异常累积,查 diagnostic_events WHERE level IN (WARN, ERROR)"},
        ))


def check_stack_null_rate(cur) -> None:
    cur.execute(f"""
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE ae.raw_data->>'stack_delta' IS NULL) AS null_count
        FROM action_events ae JOIN hands h ON h.id = ae.hand_id
        WHERE h.started_at > NOW() - INTERVAL '{WINDOW_MINUTES} minutes'
    """)
    total, nulls = cur.fetchone()
    rate = (nulls / total * 100) if total > 0 else 0
    print(f"  [5] stack_delta NULL 率近 {WINDOW_MINUTES} min: {nulls}/{total} = {rate:.1f}%")
    if total > 10 and rate > 50:
        WARNINGS_TO_EMIT.append((
            "health.stack_ocr_degraded",
            "WARN",
            {"stack_null_count": nulls, "total": total,
             "rate_pct": round(rate, 1),
             "guess": "stack OCR 抓不到数字,可能 ROI 框漂 / 字体渲染变 / 桌型变"},
        ))


def emit_warnings(conn) -> int:
    if not WARNINGS_TO_EMIT:
        print("\n✅ 所有 5 项 sanity 通过,无 WARN")
        return 0
    print(f"\n⚠️ {len(WARNINGS_TO_EMIT)} 个异常,emit 到 diagnostic_events:")
    import json
    cur = conn.cursor()
    for tag, level, payload in WARNINGS_TO_EMIT:
        print(f"  - {tag} ({level}): {payload.get('guess', '')}")
        cur.execute(
            """
            INSERT INTO diagnostic_events (hand_id, tag, level, payload, occurred_at)
            VALUES (NULL, %s, %s, %s::jsonb, NOW())
            """,
            (tag, level, json.dumps(payload, ensure_ascii=False)),
        )
    conn.commit()
    cur.close()
    return len(WARNINGS_TO_EMIT)


def main():
    print(f"Pokemir Pipeline 健康自检 [{datetime.now(timezone.utc).isoformat()}]")
    print("=" * 60)
    conn = _connect()
    cur = conn.cursor()

    check_hands_rate(cur)
    check_avg_conf_dropped(cur)
    check_override_rate(cur)
    check_diag_warn_burst(cur)
    check_stack_null_rate(cur)

    cur.close()
    n_warn = emit_warnings(conn)
    conn.close()

    sys.exit(0 if n_warn == 0 else 1)


if __name__ == "__main__":
    main()
