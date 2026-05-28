"""T29 跨 session 玩家合并候选 — 找 alias 但不动 DB(2026-05-28 立)

OCR 漂移把同一玩家拆成多个名字(豺狼I vs 豺狼I1,小雨滴答落 vs 小雨滴笞落 等)
→ 画像分散到多个 player_name,数据无法累积。

本工具:**只生成候选合并组报告**,不动 action_events。用户人眼审核后
另外授权(L4 UPDATE)再合并。

用法:
    python tools/find_player_aliases.py                  # 全默认
    python tools/find_player_aliases.py --cutoff 0.8     # 严格阈值(默认 0.75)
    python tools/find_player_aliases.py --min-hands 5    # 只看手数 ≥ 5 的(过滤短样本)

输出:
    控制台:候选合并组(分组 + 各玩家手数 + 推荐合并方向)
    文件:  tools/output/aliases_<TS>.csv(可后续 UPDATE 用)

⚠️ 不动 DB。审核后跑独立 UPDATE 工具(将来另立 task)。
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
OUTPUT_DIR = PROJECT_ROOT / "tools" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2


def _similarity(a: str, b: str) -> float:
    """case-insensitive ratio,跟 _canonicalize_player_id_map 一致策略。"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def find_alias_pairs(cutoff: float, min_hands: int) -> list[dict]:
    """Returns list of {a, b, ratio, a_hands, b_hands, recommend}."""
    dsn = os.getenv("POKEMIR_DB_DSN_SYNC")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    cur.execute("""
        SELECT player_name, COUNT(DISTINCT hand_id) AS hands
        FROM action_events
        WHERE player_name IS NOT NULL
          AND player_name NOT LIKE 'TempUser%%'
        GROUP BY player_name
        HAVING COUNT(DISTINCT hand_id) >= %s
        ORDER BY hands DESC
    """, (min_hands,))
    players = cur.fetchall()  # [(name, hands), ...]
    cur.close()
    conn.close()

    print(f"对比 {len(players)} 个玩家(手数 ≥ {min_hands}),寻找相似度 ≥ {cutoff} 的对。")

    pairs: list[dict] = []
    for i, (n_i, h_i) in enumerate(players):
        if len(n_i) < 3:
            continue
        for n_j, h_j in players[i + 1:]:
            if len(n_j) < 3:
                continue
            ratio = _similarity(n_i, n_j)
            if ratio < cutoff:
                continue
            # 推荐:手数多的 + 字符长的 = 保留(canonical),反方向 alias 合并到它
            if h_i > h_j or (h_i == h_j and len(n_i) >= len(n_j)):
                canonical, alias = n_i, n_j
                ch, ah = h_i, h_j
            else:
                canonical, alias = n_j, n_i
                ch, ah = h_j, h_i
            pairs.append({
                "alias": alias,
                "canonical": canonical,
                "ratio": round(ratio, 3),
                "alias_hands": ah,
                "canonical_hands": ch,
                "recommend": f"{alias} → {canonical}",
            })
    return pairs


def main():
    parser = argparse.ArgumentParser(description="T29 找跨 session 玩家 alias")
    parser.add_argument("--cutoff", type=float, default=0.75, help="相似度阈值 (默认 0.75)")
    parser.add_argument("--min-hands", type=int, default=1, help="最少手数过滤(默认 1)")
    args = parser.parse_args()

    pairs = find_alias_pairs(args.cutoff, args.min_hands)
    if not pairs:
        print("✅ 没找到 alias 候选(数据干净)")
        return

    # 按相似度倒序
    pairs.sort(key=lambda p: -p["ratio"])

    print(f"\n找到 {len(pairs)} 对 alias 候选(按相似度倒序):\n")
    print(f"{'alias':<20} → {'canonical':<20} ratio  手数")
    print("─" * 70)
    for p in pairs[:30]:
        print(f"{p['alias']:<20} → {p['canonical']:<20} {p['ratio']:.3f}  ({p['alias_hands']} → {p['canonical_hands']})")
    if len(pairs) > 30:
        print(f"... 还有 {len(pairs) - 30} 对(完整见 CSV)")

    # 写 CSV
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"aliases_{ts}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["alias", "canonical", "ratio", "alias_hands", "canonical_hands", "recommend"])
        writer.writeheader()
        writer.writerows(pairs)
    print(f"\n💾 完整候选 CSV: {csv_path}")

    print("\n⚠️ 本工具仅报告,不动 DB。审核后:")
    print("  - 确认 alias 列表无误")
    print("  - 单独授权 UPDATE 操作(L4 destructive)")
    print("  - (将来)写独立 UPDATE 工具,基于此 CSV 合并")


if __name__ == "__main__":
    main()
