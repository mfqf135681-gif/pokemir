"""HUD overlay window (Phase 1: stub)."""


class HUDOverlay:
    """Transparent overlay window showing HUD stats and decision suggestions.

    Phase 1: placeholder. Will use Electron + React in Phase 4-5.
    """

    def __init__(self):
        self._running = False

    def show(self):
        self._running = True

    def hide(self):
        self._running = False

    def update_stats(self, player_name: str, stats: dict):
        pass

    def update_suggestion(self, text: str):
        pass
