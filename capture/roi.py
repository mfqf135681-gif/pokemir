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

    # Hero action panel ROIs (2026-05-26 added — bottom of WePoker shows these only for hero):
    #   give_pot_button = "让池" 小圆按钮(让池给小盲)— signal for special concede
    #   free_action_button = "免费" 按钮(免费 check / 跳过显示)
    # Both optional;pipeline detection wiring is TBD.
    give_pot_button: ROIRegion | None = None
    free_action_button: ROIRegion | None = None

    # Per-seat regions (configured count may be less than num_seats during partial setup)
    seat_regions: list["SeatROI"] = field(default_factory=list)

    # Total expected seats at this table (8 / 9 / 6 ...). Read from profile JSON's
    # "num_seats" field; falls back to len(seat_regions). compute_positions uses
    # this for the button-relative modulo so partial configs still get correct
    # position labels (BTN/SB/BB/...).
    num_seats: int = 0

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
        if self.give_pot_button:
            result["give_pot_button"] = (self.give_pot_button.left, self.give_pot_button.top,
                                         self.give_pot_button.width, self.give_pot_button.height)
        if self.free_action_button:
            result["free_action_button"] = (self.free_action_button.left, self.free_action_button.top,
                                            self.free_action_button.width, self.free_action_button.height)
        for cc in self.community_cards:
            result["community_cards"].append((cc.left, cc.top, cc.width, cc.height))
        for seat in self.seat_regions:
            entry = {
                "seat_index": seat.seat_index,
                "action": (seat.action_area.left, seat.action_area.top, seat.action_area.width, seat.action_area.height),
                "stack": (seat.stack_area.left, seat.stack_area.top, seat.stack_area.width, seat.stack_area.height),
            }
            if seat.amount_area:
                entry["amount"] = (seat.amount_area.left, seat.amount_area.top,
                                   seat.amount_area.width, seat.amount_area.height)
            if seat.fold_area:
                entry["fold_area"] = (seat.fold_area.left, seat.fold_area.top,
                                       seat.fold_area.width, seat.fold_area.height)
            if seat.button_indicator:
                entry["button_indicator"] = (seat.button_indicator.left, seat.button_indicator.top,
                                             seat.button_indicator.width, seat.button_indicator.height)
            if seat.cards_area:
                entry["cards"] = (seat.cards_area.left, seat.cards_area.top, seat.cards_area.width, seat.cards_area.height)
            if seat.id_area:
                entry["id"] = (seat.id_area.left, seat.id_area.top, seat.id_area.width, seat.id_area.height)
            if seat.hand_type_area:
                entry["hand_type"] = (seat.hand_type_area.left, seat.hand_type_area.top,
                                      seat.hand_type_area.width, seat.hand_type_area.height)
            if seat.timer_area:
                entry["timer"] = (seat.timer_area.left, seat.timer_area.top,
                                  seat.timer_area.width, seat.timer_area.height)
            if seat.win_amount_area:
                entry["win_amount"] = (seat.win_amount_area.left, seat.win_amount_area.top,
                                       seat.win_amount_area.width, seat.win_amount_area.height)
            result["seats"].append(entry)
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "TableROIs":
        """Deserialize from dict. Tolerates missing/None values for optional ROIs
        (skipped during roi_config); falls back to dataclass defaults."""
        rois = cls()
        rois._window_title = data.get("window_title", "")
        rois.num_seats = int(data.get("num_seats", 0))
        if data.get("hero_card_1"):
            rois.hero_card_1 = _tuple_to_roi(data["hero_card_1"], "hero_card_1")
        if data.get("hero_card_2"):
            rois.hero_card_2 = _tuple_to_roi(data["hero_card_2"], "hero_card_2")
        if data.get("pot_size"):
            rois.pot_size = _tuple_to_roi(data["pot_size"], "pot_size")
        if data.get("give_pot_button"):
            rois.give_pot_button = _tuple_to_roi(data["give_pot_button"], "give_pot_button")
        if data.get("free_action_button"):
            rois.free_action_button = _tuple_to_roi(data["free_action_button"], "free_action_button")
        for tup in data.get("community_cards", []) or []:
            if tup:
                rois.add_community_card_roi(*tup)
        for s in data.get("seats", []):
            # Skip incomplete seat configs (multi-pass roi_config workflow may save
            # entries with only stack or only button_indicator first; pipeline silently
            # ignores them until both action+stack are present)
            if not s.get("action") or not s.get("stack"):
                continue
            seat = SeatROI(
                seat_index=s.get("seat_index", 0),
                action_area=_tuple_to_roi(s["action"], "seat_action"),
                amount_area=_tuple_to_roi(s["amount"], "seat_amount") if s.get("amount") else None,
                fold_area=_tuple_to_roi(s["fold_area"], "seat_fold") if s.get("fold_area") else None,
                stack_area=_tuple_to_roi(s["stack"], "seat_stack"),
                button_indicator=_tuple_to_roi(s["button_indicator"], "seat_btn") if s.get("button_indicator") else None,
                cards_area=_tuple_to_roi(s["cards"], "seat_cards") if s.get("cards") else None,
                id_area=_tuple_to_roi(s["id"], "seat_id") if s.get("id") else None,
                hand_type_area=_tuple_to_roi(s["hand_type"], "seat_hand_type") if s.get("hand_type") else None,
                timer_area=_tuple_to_roi(s["timer"], "seat_timer") if s.get("timer") else None,
                win_amount_area=_tuple_to_roi(s["win_amount"], "seat_win_amount") if s.get("win_amount") else None,
            )
            rois.seat_regions.append(seat)
        return rois


@dataclass
class SeatROI:
    """ROIs for a single physical seat at the table.

    WePoker layout (verified 2026-05-25 discussion):
      - action_area = above-avatar pixel zone; displays player ID when idle, replaced
        by call/raise/check/bet text when player acts. Pixel-coincident with id_area.
        WePoker shows ONLY action keyword here (e.g. "跟注"), NOT the amount.
      - amount_area = beside-avatar zone; displays chip-icon + amount number for
        call/raise/bet actions. OCR'd with digit allowlist; result concatenated to
        action_area text before parser.
      - fold_area = avatar-center zone; displays "弃牌" + avatar greys out on fold only.
      - stack_area = below-avatar; chip count (total stack).
      - id_area = pixel-coincident with action_area; OCR'd only at hand-start (before
        any action overwrites the ID display).
    """
    seat_index: int = 0
    action_area: ROIRegion = None      # above-avatar: call/raise/check/bet/post_sb/post_bb KEYWORD only
    amount_area: ROIRegion | None = None  # beside-avatar: chip-icon + amount digits
    fold_area: ROIRegion | None = None # avatar-center: "弃牌" text on fold (different pixel zone than action)
    stack_area: ROIRegion = None       # below-avatar: chip count
    button_indicator: ROIRegion | None = None  # small area showing dealer button (D icon)
    cards_area: ROIRegion | None = None  # opponent hole cards (usually not visible)
    id_area: ROIRegion | None = None   # pixel-same-as-action; OCR'd once at hand-start for player ID
    hand_type_area: ROIRegion | None = None  # 摊牌时 seat 下方显示的牌型中文文本(对子/顺子/同花/葫芦/...);
                                              # 用于和 CNN 识别的 hole + community 推导出的牌型做交叉验证
    timer_area: ROIRegion | None = None  # 决策倒计时数字专用 ROI(独立于 fold_area);
                                          # 位置固定,只显示 1-2 位数字 + "s" 或纯数字;OCR 更准确,
                                          # 跟"弃牌"/"All In"/showdown 状态不冲突.向后兼容:若 None,
                                          # pipeline fall back 到现有 fold_area regex 检测.
    win_amount_area: ROIRegion | None = None  # 获胜玩家头上短暂显示的赢取金额(+45 / +1000 等);
                                                # 只在 hand 结算 1-2 秒短暂显示;为 Path B 净胜负
                                                # 统计提供直接信号(无需 stack delta 推算).
                                                # 紧框 "+" 号 + 数字本身.


class ROIManager:
    """Load/save ROI configurations, map seats to positions using button location."""

    def __init__(self):
        self.rois: TableROIs = TableROIs()
        self.button_seat_index: int | None = None  # which seat has the dealer button

    @classmethod
    def from_json(cls, path: str) -> "ROIManager":
        """Load ROI configuration from a JSON file."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        mgr = cls()
        mgr.rois = TableROIs.from_dict(data)
        return mgr

    def to_json(self, path: str) -> None:
        """Save current ROI configuration to a JSON file."""
        data = self.rois.to_dict()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def compute_positions(self) -> dict[int, str]:
        """Given button position, compute all seat positions.

        Uses self.rois.num_seats (from profile JSON) for the modulo so that
        partial seat configs (e.g. only seat_1 + seat_7 of an 8-max table) still
        get correct BTN/SB/BB/... labels.

        Standard mappings (BTN=0, clockwise):
            6-max: BTN, SB, BB, UTG, MP, CO
            8-max: BTN, SB, BB, UTG, UTG+1, MP, HJ, CO
            9-max: BTN, SB, BB, UTG, UTG+1, MP, MP+1, CO, HJ
        """
        # Total seats at the table (from profile); fall back to configured count
        num_seats = self.rois.num_seats or len(self.rois.seat_regions)
        if num_seats == 0:
            return {}

        if self.button_seat_index is None:
            # Without a button, we can't compute positions deterministically.
            # Return empty so callers know to skip Position-dependent metadata.
            return {}

        if num_seats == 6:
            pos_order = ["BTN", "SB", "BB", "UTG", "MP", "CO"]
        elif num_seats == 8:
            pos_order = ["BTN", "SB", "BB", "UTG", "UTG+1", "MP", "HJ", "CO"]
        elif num_seats == 9:
            pos_order = ["BTN", "SB", "BB", "UTG", "UTG+1", "MP", "MP+1", "CO", "HJ"]
        else:
            # Unknown table size — skip position assignment; caller defaults to UTG/etc
            return {}

        mapping = {}
        # Only emit positions for seats actually configured in this profile.
        configured_indices = {s.seat_index for s in self.rois.seat_regions}
        for i in range(num_seats):
            if i not in configured_indices:
                continue
            relative = (i - self.button_seat_index) % num_seats
            mapping[i] = pos_order[relative] if relative < len(pos_order) else None
        return {k: v for k, v in mapping.items() if v is not None}
