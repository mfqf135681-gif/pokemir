"""ROI (Region of Interest) management for poker table layout."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from .screen import ROIRegion


def _tuple_to_roi(roi_tuple: tuple, name: str) -> ROIRegion:
    """Convert (left, top, width, height) to ROIRegion."""
    return ROIRegion(name, roi_tuple[0], roi_tuple[1], roi_tuple[2], roi_tuple[3])


@dataclass
class TableROIs:
    """Collection of ROIs for a standard poker table layout."""

    # Hero's hole cards
    hero_card_1: ROIRegion = field(default_factory=lambda: ROIRegion("hero_card_1", 0, 0, 60, 80))
    hero_card_2: ROIRegion = field(default_factory=lambda: ROIRegion("hero_card_2", 0, 0, 60, 80))

    # Community cards (up to 5)
    community_cards: list[ROIRegion] = field(default_factory=list)

    # Pot size area
    pot_size: ROIRegion = field(default_factory=lambda: ROIRegion("pot_size", 0, 0, 120, 30))

    # Per-seat regions (0-5, relative to the table layout)
    seat_regions: list["SeatROI"] = field(default_factory=list)

    def add_community_card_roi(self, left: int, top: int, width: int, height: int):
        self.community_cards.append(
            ROIRegion(f"community_{len(self.community_cards)}", left, top, width, height)
        )

    def to_dict(self) -> dict:
        """Serialize for storage."""
        result = {
            "hero_card_1": (self.hero_card_1.left, self.hero_card_1.top, self.hero_card_1.width, self.hero_card_1.height),
            "hero_card_2": (self.hero_card_2.left, self.hero_card_2.top, self.hero_card_2.width, self.hero_card_2.height),
            "pot_size": (self.pot_size.left, self.pot_size.top, self.pot_size.width, self.pot_size.height),
            "community_cards": [],
            "seats": [],
        }
        for cc in self.community_cards:
            result["community_cards"].append((cc.left, cc.top, cc.width, cc.height))
        for seat in self.seat_regions:
            entry = {
                "seat_index": seat.seat_index,
                "action": (seat.action_area.left, seat.action_area.top, seat.action_area.width, seat.action_area.height),
                "stack": (seat.stack_area.left, seat.stack_area.top, seat.stack_area.width, seat.stack_area.height),
            }
            if seat.button_indicator:
                entry["button_indicator"] = (seat.button_indicator.left, seat.button_indicator.top,
                                             seat.button_indicator.width, seat.button_indicator.height)
            if seat.cards_area:
                entry["cards"] = (seat.cards_area.left, seat.cards_area.top, seat.cards_area.width, seat.cards_area.height)
            result["seats"].append(entry)
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "TableROIs":
        """Deserialize from dict."""
        rois = cls()
        rois._window_title = data.get("window_title", "")
        rois.hero_card_1 = _tuple_to_roi(data["hero_card_1"], "hero_card_1")
        rois.hero_card_2 = _tuple_to_roi(data["hero_card_2"], "hero_card_2")
        rois.pot_size = _tuple_to_roi(data["pot_size"], "pot_size")
        for tup in data.get("community_cards", []):
            rois.add_community_card_roi(*tup)
        for s in data.get("seats", []):
            seat = SeatROI(
                seat_index=s.get("seat_index", 0),
                action_area=_tuple_to_roi(s["action"], "seat_action"),
                stack_area=_tuple_to_roi(s["stack"], "seat_stack"),
                button_indicator=_tuple_to_roi(s["button_indicator"], "seat_btn") if s.get("button_indicator") else None,
                cards_area=_tuple_to_roi(s["cards"], "seat_cards") if s.get("cards") else None,
            )
            rois.seat_regions.append(seat)
        return rois


@dataclass
class SeatROI:
    """ROIs for a single physical seat at the table."""
    seat_index: int = 0
    action_area: ROIRegion = None      # where fold/check/call/bet text appears
    stack_area: ROIRegion = None       # where chip count is displayed
    button_indicator: ROIRegion | None = None  # small area showing dealer button (D icon)
    cards_area: ROIRegion | None = None  # opponent hole cards (usually not visible)


class ROIManager:
    """Load/save ROI configurations, map seats to positions using button location."""

    def __init__(self):
        self.rois: TableROIs = TableROIs()
        self.button_seat_index: int | None = None  # which seat has the dealer button

    @classmethod
    def from_json(cls, path: str) -> "ROIManager":
        """Load ROI configuration from a JSON file."""
        with open(path) as f:
            data = json.load(f)
        mgr = cls()
        mgr.rois = TableROIs.from_dict(data)
        return mgr

    def to_json(self, path: str) -> None:
        """Save current ROI configuration to a JSON file."""
        data = self.rois.to_dict()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def compute_positions(self) -> dict[int, str]:
        """Given button position, compute all seat positions.

        Standard 6-max mapping (seat 0 = button, going clockwise):
            0=BTN, 1=SB, 2=BB, 3=UTG, 4=MP, 5=CO
        For 9-max: 0=BTN, 1=SB, 2=BB, 3=UTG, 4=UTG+1, 5=MP, 6=MP+1, 7=CO, 8=HJ
        """
        num_seats = len(self.rois.seat_regions)
        if num_seats == 0:
            return {}

        if self.button_seat_index is None:
            return {i: f"S{i}" for i in range(num_seats)}

        if num_seats == 6:
            pos_order = ["BTN", "SB", "BB", "UTG", "MP", "CO"]
        elif num_seats == 9:
            pos_order = ["BTN", "SB", "BB", "UTG", "UTG+1", "MP", "MP+1", "CO", "HJ"]
        else:
            pos_order = [f"S{(self.button_seat_index + i) % num_seats}" for i in range(num_seats)]
            return {i: pos_order[i] for i in range(num_seats)}

        mapping = {}
        for i in range(num_seats):
            relative = (i - self.button_seat_index) % num_seats
            mapping[i] = pos_order[relative] if relative < len(pos_order) else f"S{i}"
        return mapping
