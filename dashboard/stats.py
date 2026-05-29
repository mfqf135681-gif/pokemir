"""Stats query helpers — wraps Path B SQL views with safe fallback.

每个函数对应 1 个 view(详见 requirement-discussions/2026-05-27_03-01-00_
异步开发准备_家里dashboard.md §6 字段契约).

View 未实施时:
    - 查询抛 UndefinedTable / OperationalError
    - safe_query() 捕获返 []
    - 上层函数返 None,由 UI 显示"等待 view 实施"占位

实施后:函数自动开始返实数据,无需改 UI 代码.
"""

from __future__ import annotations

from typing import Optional

from .db import safe_query


# ── Existing tables (always available) ──────────────────────────────

def get_total_hands() -> int:
    """累计 hand 数(用于 sidebar / 首页 banner)."""
    rows = safe_query("SELECT COUNT(*) AS n FROM hands")
    return rows[0]["n"] if rows else 0


def get_total_events() -> int:
    rows = safe_query("SELECT COUNT(*) AS n FROM action_events")
    return rows[0]["n"] if rows else 0


def get_unique_players() -> int:
    # T64b(2026-05-29):排除 TempUser_<phash> 空座 placeholder.
    rows = safe_query(
        "SELECT COUNT(DISTINCT player_name) AS n FROM action_events "
        "WHERE player_name NOT LIKE 'TempUser_%'"
    )
    return rows[0]["n"] if rows else 0


def get_recent_hands(limit: int = 20) -> list[dict]:
    """最近的 hand,用于历史列表."""
    return safe_query(
        """
        SELECT id, started_at, ended_at, pot_size_final, hero_cards,
               (SELECT COUNT(*) FROM action_events WHERE hand_id=h.id) AS n_events
        FROM hands h
        ORDER BY started_at DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )


# ── Path B views (列契约见 §6,view 未实施时返 None)─────────────────

def get_player_vpip_pfr_af(min_hands: int = 5) -> Optional[list[dict]]:
    """v_player_vpip_pfr_af — 主画像.

    contract columns:
        player_name, n_hands, vpip_pct, pfr_pct, af, n_actions, last_seen
    """
    rows = safe_query(
        """
        SELECT player_name, n_hands, vpip_pct, pfr_pct, af, n_actions, last_seen
        FROM v_player_vpip_pfr_af
        WHERE n_hands >= :min_hands
        ORDER BY n_hands DESC
        """,
        {"min_hands": min_hands},
    )
    return rows or None


def get_player_3bet() -> Optional[list[dict]]:
    """v_player_3bet — 3-bet 统计.

    contract columns:
        player_name, n_3bet_opps, n_3bet_actual, three_bet_pct
    """
    rows = safe_query(
        "SELECT player_name, n_3bet_opps, n_3bet_actual, three_bet_pct "
        "FROM v_player_3bet ORDER BY n_3bet_opps DESC"
    )
    return rows or None


def get_player_position_stats(player_name: str) -> Optional[list[dict]]:
    """v_player_position_stats — 按位置分.

    contract columns:
        player_name, position, vpip_pct_at_pos, pfr_pct_at_pos, n_hands_at_pos
    """
    rows = safe_query(
        """
        SELECT position, vpip_pct_at_pos, pfr_pct_at_pos, n_hands_at_pos
        FROM v_player_position_stats
        WHERE player_name = :pn
        ORDER BY n_hands_at_pos DESC
        """,
        {"pn": player_name},
    )
    return rows or None


def get_player_net_winnings() -> Optional[list[dict]]:
    """v_player_net_winnings — 净胜负(基于 win_amount).

    contract columns:
        player_name, n_hands, sum_winnings, sum_buyins_inferred,
        bb_per_100, n_insurance_payouts
    """
    # T64b(2026-05-29):过滤 TempUser_ 鬼玩家.
    rows = safe_query(
        "SELECT player_name, n_hands, sum_winnings, sum_buyins_inferred, "
        "bb_per_100, n_insurance_payouts "
        "FROM v_player_net_winnings "
        "WHERE player_name NOT LIKE 'TempUser_%' "
        "ORDER BY sum_winnings DESC"
    )
    return rows or None


def get_player_insurance_full() -> Optional[list[dict]]:
    """v_player_insurance_full — 保险综合(direct 盾牌 + indirect stack).

    contract columns:
        player_name, all_in_hands, bought_insurance_confirmed,
        bought_insurance_inferred, insurance_buy_rate_confirmed_pct,
        insurance_buy_rate_inferred_pct, signals_agree
    """
    rows = safe_query(
        "SELECT player_name, all_in_hands, bought_insurance_confirmed, "
        "bought_insurance_inferred, insurance_buy_rate_confirmed_pct, "
        "insurance_buy_rate_inferred_pct, signals_agree "
        "FROM v_player_insurance_full ORDER BY all_in_hands DESC"
    )
    return rows or None
