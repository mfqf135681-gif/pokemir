"""Preflop statistics calculator."""

from dataclasses import dataclass, field


@dataclass
class PreflopStatsResult:
    player_name: str
    total_hands: int = 0
    vpip: float = 0.0
    pfr: float = 0.0
    af: float = 0.0
    three_bet_pct: float = 0.0
    fold_to_three_bet_pct: float = 0.0
    ats: float = 0.0
    call_open_pct: float = 0.0


class PreflopStats:
    """Compute preflop stats from a list of ActionEvents (Phase 1: stub)."""

    def compute(self, player_name: str, events: list, lookback: int = 0) -> PreflopStatsResult:
        """Phase 1 stub — returns zeros. Phase 3 will implement full calculation."""
        return PreflopStatsResult(player_name=player_name)
