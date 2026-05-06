"""Postflop statistics calculator."""

from dataclasses import dataclass


@dataclass
class PostflopStatsResult:
    player_name: str
    cbet_pct: float = 0.0
    fold_to_cbet_pct: float = 0.0
    raise_cbet_pct: float = 0.0
    wtsd_pct: float = 0.0
    wsd_pct: float = 0.0
    double_barrel_pct: float = 0.0
    check_raise_pct: float = 0.0
    donk_bet_pct: float = 0.0


class PostflopStats:
    """Compute postflop stats from a list of ActionEvents (Phase 1: stub)."""

    def compute(self, player_name: str, events: list, lookback: int = 0) -> PostflopStatsResult:
        return PostflopStatsResult(player_name=player_name)
