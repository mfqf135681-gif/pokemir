"""Interactive ROI configuration tool using OpenCV selectROI.

Usage:
    python tools/roi_config.py              # create/overwrite a profile
    python tools/roi_config.py --name party_poker
    python tools/roi_config.py --verify     # preview existing profile
"""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from capture.screen import ScreenCapturer

ROI_PROMPTS = [
    ("hero_card_1", "Hero Card 1 — drag rectangle, SPACE to confirm, C to skip"),
    ("hero_card_2", "Hero Card 2"),
    ("community_1", "Community Card 1"),
    ("community_2", "Community Card 2"),
    ("community_3", "Community Card 3"),
    ("community_4", "Community Card 4"),
    ("community_5", "Community Card 5"),
    ("pot_size", "Pot Size Area"),
]

def _get_seat_labels(num_seats: int) -> list[str]:
    """Generate seat labels for the given table size.

    Seats are numbered clockwise starting from top-left on screen.
    Positions (BTN/SB/BB/...) are assigned dynamically at runtime.
    """
    if num_seats == 6:
        return ["Seat 0 (top-left)", "Seat 1 (top-right)",
                "Seat 2 (right)", "Seat 3 (bottom-right)",
                "Seat 4 (bottom-left)", "Seat 5 (left)"]
    elif num_seats == 9:
        return ["Seat 0 (top-left)", "Seat 1 (top)", "Seat 2 (top-right)",
                "Seat 3 (right)", "Seat 4 (bottom-right)", "Seat 5 (bottom)",
                "Seat 6 (bottom-left)", "Seat 7 (left)", "Seat 8 (center-left)"]
    else:
        return [f"Seat {i}" for i in range(num_seats)]


def select_roi(window_name: str, img: np.ndarray) -> tuple | None:
    """Open cv2.selectROI window. Returns (x, y, w, h) or None if skipped."""
    print(f"  {window_name}...")
    r = cv2.selectROI(window_name, img, showCrosshair=True)
    cv2.destroyWindow(window_name)
    x, y, w, h = r
    if w == 0 and h == 0:
        return None
    return (x, y, w, h)


VALID_FIELDS = {"hero_card_1", "hero_card_2", "pot_size"} | {
    f"community_{i}" for i in range(1, 6)
} | {
    f"seat_{i}" for i in range(9)
}


def main():
    parser = argparse.ArgumentParser(description="Poker ROI Configuration Tool")
    parser.add_argument("--name", default="default", help="Profile name (default: default)")
    parser.add_argument("--seats", type=int, default=6, help="Number of seats (6 or 9, default: 6)")
    parser.add_argument("--window", default="", help="Window title substring to find (e.g. 'Poker' or 'GGPoker')")
    parser.add_argument("--verify", action="store_true", help="Preview existing ROI config")
    parser.add_argument(
        "--field",
        default=None,
        choices=sorted(VALID_FIELDS),
        help="Incremental mode: configure ONE field in the existing profile and "
             "merge with the rest. Avoids the 'roi_config wipes everything else' "
             "footgun. Example: --field pot_size. Profile file must already exist.",
    )
    args = parser.parse_args()

    capturer = ScreenCapturer()

    # ── Find or select capture source ────────────────────
    if args.window:
        if not capturer.find_window_by_title(args.window):
            print(f"ERROR: No window found matching '{args.window}'")
            print("  List of visible windows:")
            _list_windows()
            return 1
    else:
        # Interactive window selection
        print("Searching for poker client windows...")
        candidates = _find_poker_windows()
        if candidates:
            print("\nFound these candidate windows:")
            for i, w in enumerate(candidates):
                print(f"  [{i}] {w['title']}  ({w['width']}x{w['height']})")
            print(f"  [{len(candidates)}] None — use full monitor instead")
            choice = input(f"\nSelect [0-{len(candidates)}]: ").strip()
            if choice.isdigit():
                idx = int(choice)
                if 0 <= idx < len(candidates):
                    capturer.find_window_by_title(candidates[idx]["title"])
        else:
            print("No poker-related windows found, using full monitor.")
            capturer.select_monitor(1)

    output_dir = Path(__file__).parent.parent / "rois"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"{args.name}.json"

    # ── Verify mode ──────────────────────────────────────
    if args.verify:
        if not output_path.exists():
            print(f"ERROR: {output_path} not found. Run without --verify first.")
            return 1
        with open(output_path) as f:
            data = json.load(f)
        # Find window if title saved
        if data.get("window_title"):
            if not capturer.find_window_by_title(data["window_title"]):
                print(f"WARNING: Window '{data['window_title']}' not found, using monitor")
                capturer.select_monitor(1)
        else:
            capturer.select_monitor(1)
        img = capturer.capture()
        img_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        _draw_rois(img_bgr, data)
        cv2.imshow(f"ROI Preview — {data.get('window_title', args.name)} (press any key)", img_bgr)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
        return 0

    # ── Capture reference screenshot (window or monitor) ─
    print("\nTaking reference screenshot...")
    full = capturer.capture()
    img = cv2.cvtColor(full, cv2.COLOR_BGRA2BGR)
    print(f"  Resolution: {img.shape[1]}x{img.shape[0]}\n")

    # ── Incremental --field mode ─────────────────────────
    if args.field:
        if not output_path.exists():
            print(f"ERROR: {output_path} not found. --field requires an existing profile.")
            return 1
        with open(output_path) as f:
            existing = json.load(f)
        print(f"Loaded existing profile {output_path.name}")
        print(f"Configuring only: {args.field}")

        # seat_N: collect 5 ROIs (action / stack / button_indicator / cards / id) in one invocation.
        # Existing seat with same seat_index is replaced; otherwise appended.
        if args.field.startswith("seat_"):
            idx = int(args.field.split("_")[1])
            print(f"\n--- Seat {idx}: 5 ROIs (ESC/C to skip individual ones) ---")
            action_rect = select_roi(f"Seat {idx} — Action Text (FOLD/CHECK/CALL/BET/跟注/加注/弃牌)", img)
            stack_rect = select_roi(f"Seat {idx} — Stack Amount", img)
            btn_rect = select_roi(f"Seat {idx} — Button Indicator (small 'D' icon area; ESC to skip)", img)
            cards_rect = select_roi(f"Seat {idx} — Cards Area (optional showdown reveal; ESC to skip)", img)
            id_rect = select_roi(f"Seat {idx} — User ID (platform digit ID; ESC to skip)", img)

            if not action_rect or not stack_rect:
                # Both are required: capture/roi.py from_dict() calls
                # _tuple_to_roi(s["action"], ...) and s["stack"] unconditionally,
                # so a half-configured seat would crash pipeline load.
                print(f"Skipped — seat_{idx} requires BOTH action AND stack ROIs; nothing changed.")
                cv2.destroyAllWindows()
                return 0

            seat_entry = {
                "seat_index": idx,
                "action": list(action_rect) if action_rect else None,
                "stack": list(stack_rect) if stack_rect else None,
                "button_indicator": list(btn_rect) if btn_rect else None,
                "cards": list(cards_rect) if cards_rect else None,
                "id": list(id_rect) if id_rect else None,
            }
            seats = existing.get("seats") or []
            # Replace existing by seat_index, else append
            replaced = False
            for i, s in enumerate(seats):
                if s.get("seat_index") == idx:
                    seats[i] = seat_entry
                    replaced = True
                    break
            if not replaced:
                seats.append(seat_entry)
            seats.sort(key=lambda s: s.get("seat_index", 0))
            existing["seats"] = seats
            cv2.destroyAllWindows()
            with open(output_path, "w") as f:
                json.dump(existing, f, indent=2)
            print(f"\nseat_{idx} {'updated' if replaced else 'added'} in {output_path}")
            print(f"  action={bool(action_rect)} stack={bool(stack_rect)} button={bool(btn_rect)} cards={bool(cards_rect)} id={bool(id_rect)}")
            print(f"Verify with: python tools/roi_config.py --verify --name {args.name}")
            return 0

        prompt = f"{args.field.replace('_', ' ').title()} — drag rect, SPACE to confirm, C to skip"
        rect = select_roi(prompt, img)
        if rect is None:
            print(f"Skipped — {args.field} not changed.")
            cv2.destroyAllWindows()
            return 0
        if args.field.startswith("community_"):
            idx = int(args.field.split("_")[1]) - 1  # community_1 → index 0
            cc = existing.get("community_cards") or []
            while len(cc) <= idx:
                cc.append(None)
            cc[idx] = list(rect)
            existing["community_cards"] = cc
        else:
            existing[args.field] = list(rect)
        cv2.destroyAllWindows()
        with open(output_path, "w") as f:
            json.dump(existing, f, indent=2)
        print(f"\n{args.field} updated in {output_path}")
        print(f"Verify with: python tools/roi_config.py --verify --name {args.name}")
        return 0

    # ── Select ROIs ──────────────────────────────────────
    print("For each ROI, drag a rectangle and press SPACE to confirm.")
    print("Press C or ESC to skip an optional ROI.\n")

    result = {
        "name": args.name,
        "num_seats": args.seats,
        "window_title": args.window or capturer._found_title,
        "resolution": [img.shape[1], img.shape[0]],
        "hero_card_1": None,
        "hero_card_2": None,
        "community_cards": [],
        "pot_size": None,
        "seats": [],
    }

    # Standard ROIs
    for key, prompt in ROI_PROMPTS:
        rect = select_roi(prompt, img)
        if rect is None:
            continue
        if key.startswith("community_"):
            result["community_cards"].append(rect)
        else:
            result[key] = rect

    # Per-seat ROIs
    seat_labels = _get_seat_labels(args.seats)
    for i, seat_name in enumerate(seat_labels):
        print(f"\n--- {seat_name} ---")
        action_rect = select_roi(f"Seat {i} — Action Text (FOLD/CHECK/CALL/BET)", img)
        stack_rect = select_roi(f"Seat {i} — Stack Amount", img)
        btn_rect = select_roi(f"Seat {i} — Button Indicator (small 'D' icon area, ESC to skip)", img)
        cards_rect = select_roi(f"Seat {i} — Cards Area (optional, ESC to skip)", img)
        id_rect = select_roi(f"Seat {i} — User ID (platform digit ID; ESC to skip)", img)

        if action_rect or stack_rect:
            result["seats"].append({
                "seat_index": i,
                "action": action_rect,
                "stack": stack_rect,
                "button_indicator": btn_rect,
                "cards": cards_rect,
                "id": id_rect,
            })

    # ── Save ─────────────────────────────────────────────
    cv2.destroyAllWindows()

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nROI configuration saved to: {output_path}")
    print(f"  Hero cards: {bool(result['hero_card_1'])}/{bool(result['hero_card_2'])}")
    print(f"  Community cards: {len(result['community_cards'])}")
    print(f"  Seats: {len(result['seats'])}")
    return 0


def _draw_rois(img: np.ndarray, data: dict):
    """Draw colored rectangles for each ROI on the image."""
    colors = {
        "hero": (0, 255, 0),       # green
        "community": (255, 0, 0),  # blue
        "pot": (0, 255, 255),      # yellow
        "button": (255, 0, 255),   # magenta
        "seat": (0, 165, 255),     # orange
    }

    def draw_rect(tup, color, label=""):
        if tup is None:
            return
        x, y, w, h = tup
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
        if label:
            cv2.putText(img, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

    draw_rect(data.get("hero_card_1"), colors["hero"], "Hero 1")
    draw_rect(data.get("hero_card_2"), colors["hero"], "Hero 2")
    draw_rect(data.get("pot_size"), colors["pot"], "Pot")

    for cc in data.get("community_cards", []):
        draw_rect(cc, colors["community"], "Comm")

    for s in data.get("seats", []):
        label = f"Seat {s.get('seat_index', '?')}"
        draw_rect(s.get("action"), colors["seat"], label)
        draw_rect(s.get("stack"), colors["seat"])
        draw_rect(s.get("cards"), colors["seat"])
        draw_rect(s.get("button_indicator"), colors["button"], "BTN?" if s.get("button_indicator") else "")
        draw_rect(s.get("id"), colors["seat"], "ID" if s.get("id") else "")


def _find_poker_windows() -> list[dict]:
    """Return a list of visible windows whose titles suggest a poker client."""
    poker_keywords = ["poker", "holdem", "hold'em", "德州", "扑克", "GG", "pokerstars",
                      "party", "888", "natural8", "wpk", "pokertime", "clubgg"]
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        results = []

        @WNDENUMPROC
        def enum_callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if any(kw in title.lower() for kw in poker_keywords):
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                results.append({
                    "title": title,
                    "left": rect.left,
                    "top": rect.top,
                    "width": rect.right - rect.left,
                    "height": rect.bottom - rect.top,
                })
            return True

        user32.EnumWindows(enum_callback, 0)
        return results
    except Exception:
        return []


def _list_windows():
    """Print all visible window titles for debugging."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        @WNDENUMPROC
        def enum_callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value.strip():
                print(f"  {buf.value!r}")
            return True

        user32.EnumWindows(enum_callback, 0)
    except Exception:
        print("  (unable to list windows)")


if __name__ == "__main__":
    sys.exit(main())
