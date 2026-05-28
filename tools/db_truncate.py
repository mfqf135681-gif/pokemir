"""清空 pokemir 数据表(不动 schema / view)。

⚠️ DESTRUCTIVE — 仅在用户明确授权(2026-05-28 T9 验证场景)下使用。
确认 prompt 双保险:必须键入 "yes" 才执行。

清空对象(6 张):
    hands, action_events, diagnostic_events,
    player_stats_cache, player_situational_stats, replay_corrections

不动:
    - 所有 view(v_*)
    - 表 schema
    - rois/*.json 等配置

用法(Win 桌面机):
    cd D:\\project\\pokemir
    .venv\\Scripts\\activate
    python tools/db_truncate.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import psycopg2
except ImportError:
    print("❌ psycopg2 未装,请先 pip install psycopg2-binary")
    sys.exit(1)

TABLES = [
    "hands",
    "action_events",
    "diagnostic_events",
    "player_stats_cache",
    "player_situational_stats",
    "replay_corrections",
]


def _counts(cur, tables):
    out = {}
    for t in tables:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        out[t] = cur.fetchone()[0]
    return out


def main():
    dsn = os.getenv("POKEMIR_DB_DSN_SYNC")
    if not dsn:
        print("❌ POKEMIR_DB_DSN_SYNC 未配置 — 检查 .env 文件")
        sys.exit(1)

    print("⚠️  即将清空以下 6 张数据表:")
    for t in TABLES:
        print(f"   - {t}")
    print("\n   schema / view / rois / 配置 不动。")
    print("   操作 不可逆。\n")

    ans = input("确认清空?键入 'yes' 继续(任何其他键放弃): ").strip().lower()
    if ans != "yes":
        print("放弃。数据未动。")
        sys.exit(0)

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()

    before = _counts(cur, TABLES)
    print("\n清空前:")
    for t, n in before.items():
        print(f"   {t}: {n}")
    total = sum(before.values())
    print(f"   总计: {total} 行")

    cur.execute(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE")

    after = _counts(cur, TABLES)
    print("\n清空后:")
    for t, n in after.items():
        print(f"   {t}: {n}")
    print(f"   总计: {sum(after.values())} 行")

    cur.close()
    conn.close()

    if sum(after.values()) == 0:
        print("\n✅ 完成,所有数据表已清空。")
    else:
        print("\n⚠️ 部分表未清空,请检查 FK 约束或权限。")
        sys.exit(2)


if __name__ == "__main__":
    main()
