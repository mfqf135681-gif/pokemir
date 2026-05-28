"""Interactive ROI configuration tool using OpenCV selectROI.

Usage:
    python tools/roi_config.py              # create/overwrite a profile
    python tools/roi_config.py --name party_poker_9
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

    Convention (2026-05-25 fixed, hero-centric clockwise = poker action order):
      seat_0 = hero (bottom-center; even in observer mode this is the position
               where the user would sit if they sat down)
      seat_N = N-th seat clockwise from hero (N=1..num_seats-1)

    "Clockwise" here follows the physical clock-hand direction in a top-down
    table view: from 6 o'clock (hero/bottom) → 9 o'clock (screen LEFT) → 12
    o'clock (top) → 3 o'clock (screen right). This matches the standard poker
    action order (BTN → SB → BB → UTG → ... is left of BTN on screen for
    hero-bottom tables).

    BTN/SB/BB positions are still computed dynamically at runtime from the
    detected dealer button location.
    """
    if num_seats == 6:
        return ["Seat 0 (you / bottom-center)",
                "Seat 1 (1 clockwise — bottom-LEFT / your left neighbour)",
                "Seat 2 (2 clockwise — upper-left)",
                "Seat 3 (3 clockwise — top)",
                "Seat 4 (4 clockwise — upper-right)",
                "Seat 5 (5 clockwise — bottom-right / your right neighbour)"]
    elif num_seats == 8:
        # 8-max: hero + 7 others; top has 1 player centered (the key difference vs 9-max).
        return ["Seat 0 (you / bottom-center)",
                "Seat 1 (1 clockwise — bottom-LEFT / your left neighbour)",
                "Seat 2 (2 clockwise — left-side mid)",
                "Seat 3 (3 clockwise — upper-left)",
                "Seat 4 (4 clockwise — TOP-center, the only top seat in 8-max)",
                "Seat 5 (5 clockwise — upper-right)",
                "Seat 6 (6 clockwise — right-side mid)",
                "Seat 7 (7 clockwise — bottom-right / your right neighbour)"]
    elif num_seats == 9:
        # 9-max: hero + 8 others; top has 2 players side-by-side (vs 8-max single).
        return ["Seat 0 (you / bottom-center)",
                "Seat 1 (1 clockwise — bottom-LEFT / your left neighbour)",
                "Seat 2 (2 clockwise — left-side mid)",
                "Seat 3 (3 clockwise — upper-left)",
                "Seat 4 (4 clockwise — top-LEFT of the two top seats)",
                "Seat 5 (5 clockwise — top-RIGHT of the two top seats)",
                "Seat 6 (6 clockwise — upper-right)",
                "Seat 7 (7 clockwise — right-side mid)",
                "Seat 8 (8 clockwise — bottom-right / your right neighbour)"]
    else:
        return [f"Seat {i} ({'you' if i == 0 else f'{i} clockwise from you'})" for i in range(num_seats)]


def _fit_window(win_name: str, img: np.ndarray,
                max_w: int = 1280, max_h: int = 720) -> None:
    """Pre-create cv2 window and auto-fit to max_w × max_h while preserving aspect.

    Solves the "screenshot is 1920×1080 but my screen is 1366×768, cv2 window
    blows past screen edge" zoom issue.  cv2.WINDOW_NORMAL lets user further
    resize if needed.

    IMPORTANT: cv2 mouse callback returns coordinates in ORIGINAL image space
    regardless of window display size — so this resize doesn't affect mouse
    coordinate math downstream.
    """
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    h, w = img.shape[:2]
    if w > max_w or h > max_h:
        scale = min(max_w / w, max_h / h)
        cv2.resizeWindow(win_name, int(w * scale), int(h * scale))


def select_roi(window_name: str, img: np.ndarray) -> tuple | None:
    """Open cv2.selectROI window. Returns (x, y, w, h) or None if skipped."""
    print(f"  {window_name}...")
    _fit_window(window_name, img)
    r = cv2.selectROI(window_name, img, showCrosshair=True)
    cv2.destroyWindow(window_name)
    x, y, w, h = r
    if w == 0 and h == 0:
        return None
    return (x, y, w, h)


def place_roi_by_click(img: np.ndarray, ref_w: int, ref_h: int,
                       seat_idx: int, hint: str = "") -> tuple | None:
    """Click-to-save ROI placement:
    - Mouse MOVE: rectangle of fixed (ref_w × ref_h) follows cursor in real-time.
    - Mouse LEFT CLICK: **immediately save** at the clicked position + advance.
    - ESC: skip this seat (preserve old value).
    - Q: quit entire batch (preserve seats already saved).

    Returns (x, y, w, h) for the placed rectangle, or None if skipped/quit.
    "__QUIT__" sentinel if user hit Q.
    """
    win = f"seat_{seat_idx}  |  size {ref_w}×{ref_h}  |  CLICK = SAVE  |  ESC=skip  Q=quit"
    state = {"cx": img.shape[1] // 2, "cy": img.shape[0] // 2,
             "clicked": False, "quit": False}

    def mouse_cb(event, x, y, flags, param):
        if event == cv2.EVENT_MOUSEMOVE:
            state["cx"], state["cy"] = x, y
        elif event == cv2.EVENT_LBUTTONDOWN:
            # Lock in click position + signal save
            state["cx"], state["cy"] = x, y
            state["clicked"] = True

    _fit_window(win, img)
    cv2.setMouseCallback(win, mouse_cb)

    half_w, half_h = ref_w // 2, ref_h // 2
    while True:
        disp = img.copy()
        x0, y0 = state["cx"] - half_w, state["cy"] - half_h
        x1, y1 = state["cx"] + half_w, state["cy"] + half_h
        cv2.rectangle(disp, (x0, y0), (x1, y1), (0, 255, 0), 2)
        cv2.drawMarker(disp, (state["cx"], state["cy"]),
                       (0, 0, 255), cv2.MARKER_CROSS, 14, 2)
        cv2.putText(disp, f"seat_{seat_idx}: 左键点击该 seat id 文字中心 = 立即保存",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.putText(disp, "ESC=跳过本 seat  /  Q=退出批量",
                    (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 255, 200), 2)
        if hint:
            cv2.putText(disp, hint[:80], (10, 90), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (200, 200, 255), 1)
        cv2.imshow(win, disp)

        if state["clicked"]:
            # Flash yellow briefly for visual confirmation, then close
            cv2.rectangle(disp, (x0, y0), (x1, y1), (0, 255, 255), 4)
            cv2.imshow(win, disp)
            cv2.waitKey(150)
            cv2.destroyWindow(win)
            return (state["cx"] - half_w, state["cy"] - half_h, ref_w, ref_h)

        key = cv2.waitKey(20) & 0xFF
        if key == 27:  # ESC
            cv2.destroyWindow(win)
            return None
        if key in (ord('q'), ord('Q')):
            cv2.destroyWindow(win)
            return ("__QUIT__",)


VALID_FIELDS = {
    "hero_card_1", "hero_card_2", "pot_size",
    "give_pot_button", "free_action_button",  # 2026-05-26 added (hero action panel)
} | {
    f"community_{i}" for i in range(1, 6)
} | {
    f"seat_{i}" for i in range(9)
}

# Per-seat sub-element names for --element flag. Order matches the full-seat prompt order.
# REQUIRED_SEAT_ELEMENTS = {action, stack} — final-save validation refuses entries lacking these.
SEAT_ELEMENT_ORDER = ["action", "amount", "fold_area", "timer", "stack", "button_indicator", "cards", "id", "hand_type", "win_amount"]
REQUIRED_SEAT_ELEMENTS = {"action", "stack"}
ELEMENT_HINTS = {
    "action": "头像上方,玩家行动时**只显示动作汉字**(「跟注/加注/下注/过牌」);WePoker 中**金额不在此**;空闲时此位置显示玩家昵称",
    "amount": "头像**旁边**,显示筹码图标 + 金额数字(call/raise/bet 时出现的本次下注额);OCR 用 digit allowlist 自动过滤图标;ESC 可跳过(fold/check 不需要金额)",
    "fold_area": "头像正中,玩家弃牌时显示「弃牌」两字 + 头像变灰(独立于上方动作区)",
    "timer": "**决策倒计时数字专用 ROI**(独立于 fold_area)— 位置固定,玩家轮到行动时显示 1-2 位数字(如「10」「9」「15」等),可能带「s」单位;**只框那 1-2 位数字本身**,**不带头像、不带其他文字**;比 fold_area **小得多**,OCR 更聚焦;若 None pipeline 会 fall back 到现有 fold_area regex 检测;ESC 可跳过(保持旧行为)",
    "stack": "头像下方的筹码量数字(玩家总筹码,与 amount 不同)",
    "button_indicator": "玩家筹码量数字**左侧紧贴**的小 D 标记(轮换 dealer 标志,~10-20 像素);本次默认走 OCR 识别「D」字符;ESC 可跳过",
    "cards": "**摊牌底牌显示区**(showdown 时该 seat 玩家的 2 张底牌)— 紧贴牌外缘,**不能含桌面色**(上沿离牌顶白边 1-2 px 内,绝不要高);只框 2 卡本身,**不要把下方「对子」之类的牌型 badge 框进来**(那是 hand_type 独立 ROI)",
    "id": "玩家昵称区域 — WePoker 显示中文/英文/数字混排昵称(如「白鸢飞ix」「湖南闷高」),与 action 同像素;直接框跟 action 一模一样的区域即可;ESC 可跳过(将来 hand-start 缓存昵称用)",
    "hand_type": "**摊牌时**该 seat 在底牌下方显示的**牌型中文文字**(「对子」「顺子」「同花」「葫芦」「四条」「同花顺」「皇家同花顺」「三条」「两对」「高牌」);独立 ROI,**比 cards 紧得多**,只框那一行牌型文字;ESC 可跳过(若用户暂不需要交叉验证),将来用于 hole+community 推导验证",
    "win_amount": "**手结算时**获胜玩家头上短暂显示的赢取金额(「+45」「+1000」「+12500」等),只显示 1-2 秒;紧框「+」号 + 数字本身,**不带其他文字 / 头像**;为 Path B 净胜负 stat 提供直接信号(免去 stack-delta 推算的误差);全 8 seat 同样大小,适合 --copy-size 批量框",
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
    parser.add_argument(
        "--element",
        default=None,
        choices=SEAT_ELEMENT_ORDER,
        help=("Used with --field seat_N: pick ONE sub-ROI to (re)frame this run. "
              "Example: --field seat_4 --element fold_area. Without --element, all "
              "6 seat ROIs are prompted in sequence. Existing values for un-prompted "
              "ROIs are preserved."),
    )
    parser.add_argument(
        "--all-seats",
        action="store_true",
        help=("Batch mode: loop through ALL seats (0..num_seats-1, from existing "
              "profile) and prompt every SEAT_ELEMENT for each. Merges with existing "
              "ROIs (ESC each prompt to keep prior value). Use to bring a new profile "
              "from empty seats:[] to fully configured in one go."),
    )
    parser.add_argument(
        "--copy-size",
        action="store_true",
        help=("Click-to-place batch mode: drag reference rect ONCE (any seat) to "
              "set size, then loop seat_0..seat_N-1 with a rectangle of that fixed "
              "size following your mouse — left-click center, SPACE to save, ESC "
              "to skip, Q to quit batch. Requires --element <name>. Use when all "
              "seats need same-size ROI (e.g. wider id_area for long usernames)."),
    )
    parser.add_argument(
        "--from-image",
        default=None,
        help=("Read pixels from a PNG file (e.g. resource/showdown.png) instead of "
              "live screen capture. Use to configure ROIs for sparse / transient UI "
              "events (showdown cards, insurance popup, etc) by first taking a "
              "Win+Shift+S screenshot of the relevant moment and saving to disk. "
              "Combines with --field / --element / --all-seats as usual."),
    )
    args = parser.parse_args()

    capturer = ScreenCapturer()

    # ── --from-image: skip live capture, use a saved PNG instead ─────
    image_override = None
    if args.from_image:
        from_path = Path(args.from_image)
        if not from_path.exists():
            print(f"ERROR: --from-image file not found: {from_path}")
            return 1
        loaded = cv2.imread(str(from_path))
        if loaded is None:
            print(f"ERROR: cv2 failed to load image: {from_path}")
            return 1
        # imread returns BGR;tool expects BGRA-or-BGR;keep as BGR
        image_override = loaded
        print(f"Using static image: {from_path} ({loaded.shape[1]}x{loaded.shape[0]})")
    else:
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
        with open(output_path, encoding="utf-8") as f:
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

    # ── Reference image: live capture or static --from-image ─
    if image_override is not None:
        img = image_override
        print(f"  Source: --from-image  ({img.shape[1]}x{img.shape[0]})\n")
    else:
        print("\nTaking reference screenshot...")
        full = capturer.capture()
        img = cv2.cvtColor(full, cv2.COLOR_BGRA2BGR)
        print(f"  Resolution: {img.shape[1]}x{img.shape[0]}\n")

    # ── --all-seats batch mode ───────────────────────────
    # Two sub-modes (driven by presence of --element):
    #   (a) --all-seats              → loop seats × all 7 elements (seat-major, 56 prompts)
    #   (b) --all-seats --element X  → loop seats × ONE element X  (element-major, 8 prompts)
    # Mode (b) is preferred when the user wants to focus on one element type at a time
    # (e.g. "this round I frame all stacks");7 such commands = full table setup.
    if args.all_seats:
        if not output_path.exists():
            print(f"ERROR: {output_path} not found. --all-seats requires an existing profile.")
            return 1
        with open(output_path, encoding="utf-8") as f:
            existing = json.load(f)
        num_seats = int(existing.get("num_seats", 0))
        if num_seats == 0:
            print("ERROR: profile num_seats is 0 or missing.")
            return 1

        # Pick element subset: single (if --element) or all (default)
        if args.element:
            elements_to_prompt = [args.element]
            print(f"Element-major batch: ROUND = {args.element.upper()}  |  seats 0..{num_seats - 1}")
        else:
            elements_to_prompt = SEAT_ELEMENT_ORDER
            print(f"Full batch: seats 0..{num_seats - 1}  |  7 elements each")
        print(f"  Tip: ESC any prompt to skip + keep existing value;")
        print(f"       Ctrl+C any time to abort (already-saved seats persist).\n")

        seats = existing.get("seats") or []

        for idx in range(num_seats):
            # Find pre-existing entry for this seat_index
            prev_entry = None
            prev_idx_in_list = None
            for i, s in enumerate(seats):
                if s.get("seat_index") == idx:
                    prev_entry = s
                    prev_idx_in_list = i
                    break

            print(f"\n{'=' * 56}")
            if len(elements_to_prompt) == 1:
                print(f"  SEAT {idx} of {num_seats - 1} — framing {elements_to_prompt[0]}")
            else:
                print(f"  SEAT {idx} of {num_seats - 1} — framing {len(elements_to_prompt)} elements")
            print(f"  (ESC to skip + keep existing)")
            print(f"{'=' * 56}")

            captured = {}
            for elem in elements_to_prompt:
                print(f"\n▶ NOW FRAMING:  seat_{idx} → {elem.upper()}")
                print(f"  位置说明:    {ELEMENT_HINTS[elem]}")
                print(f"  操作:        鼠标拖框 → 按 SPACE 确认 / 按 ESC 跳过(保留旧值)")
                rect = select_roi(f"seat_{idx} — {elem}", img)
                captured[elem] = rect

            # Build entry: prev + captured (captured wins where non-None)
            seat_entry = dict(prev_entry) if prev_entry else {"seat_index": idx}
            for elem in elements_to_prompt:
                if captured[elem] is not None:
                    seat_entry[elem] = list(captured[elem])

            # Save partial entries too (parser skips incomplete);
            # update or append in seats list
            if prev_idx_in_list is not None:
                seats[prev_idx_in_list] = seat_entry
            else:
                seats.append(seat_entry)

            # Persist after EVERY seat (so Ctrl+C mid-way doesn't lose progress)
            seats.sort(key=lambda s: s.get("seat_index", 0))
            existing["seats"] = seats
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)

            configured = [k for k in SEAT_ELEMENT_ORDER if seat_entry.get(k)]
            missing = [e for e in REQUIRED_SEAT_ELEMENTS if not seat_entry.get(e)]
            status = "✓ ready" if not missing else f"⚠ missing {missing}"
            print(f"\n  seat_{idx} saved: configured={configured} [{status}]")

        cv2.destroyAllWindows()
        print(f"\n\nAll seats processed. Run --verify --name {args.name} to inspect.")
        return 0

    # ── --copy-size batch mode ───────────────────────────
    # 1 drag (any seat, size only) + N clicks (one per seat) instead of N drags.
    # Requires --element to specify which seat sub-ROI to batch.
    if args.copy_size:
        if not args.element:
            print("ERROR: --copy-size requires --element <name>")
            return 1
        if not output_path.exists():
            print(f"ERROR: {output_path} not found. --copy-size requires existing profile.")
            return 1
        with open(output_path, encoding="utf-8") as f:
            existing = json.load(f)
        num_seats = int(existing.get("num_seats", args.seats))
        print(f"Loaded {output_path.name}; num_seats={num_seats}")
        print(f"Batching '{args.element}' across {num_seats} seats with click-to-place.\n")
        print(f"  Hint: {ELEMENT_HINTS.get(args.element, '')}\n")

        # Phase 1: drag reference rectangle (any seat) — we only keep size
        print("STEP 1 / 2 — drag a reference rect on ANY seat to set size:")
        ref_rect = select_roi(
            f"Reference '{args.element}': drag rect (size matters, position ignored) — SPACE confirm",
            img,
        )
        if ref_rect is None:
            print("Skipped — no reference size selected.")
            cv2.destroyAllWindows()
            return 0
        _, _, ref_w, ref_h = ref_rect
        print(f"  Reference size locked: {ref_w}×{ref_h}\n")

        # Phase 2: click-to-place for each seat
        print(f"STEP 2 / 2 — for each seat 0..{num_seats - 1}:")
        print(f"  鼠标移动 = 矩形预览(中心=鼠标);SPACE 保存;ESC 跳过此 seat;Q 退出 batch\n")

        seats = existing.get("seats") or []
        seats_by_idx = {s.get("seat_index"): s for s in seats}
        n_saved, n_skipped, n_quit = 0, 0, 0

        for sidx in range(num_seats):
            placed = place_roi_by_click(img, ref_w, ref_h, sidx,
                                         hint=ELEMENT_HINTS.get(args.element, ""))
            if placed is None:
                print(f"  seat_{sidx}: ESC → 跳过(保留旧值)")
                n_skipped += 1
                continue
            if isinstance(placed, tuple) and len(placed) == 1 and placed[0] == "__QUIT__":
                print(f"  seat_{sidx}: Q → 退出 batch(seat_{sidx} 及后续未保存)")
                n_quit = num_seats - sidx
                break

            # Merge into seats list (preserve other elements; update only --element key)
            entry = seats_by_idx.get(sidx) or {"seat_index": sidx}
            entry[args.element] = list(placed)
            seats_by_idx[sidx] = entry
            n_saved += 1
            print(f"  seat_{sidx}: ✓ saved ({placed[0]},{placed[1]}) size {ref_w}×{ref_h}")

        # Save back
        new_seats = sorted(seats_by_idx.values(), key=lambda s: s.get("seat_index", 0))
        existing["seats"] = new_seats
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        cv2.destroyAllWindows()
        print(f"\n✓ Batch saved: {n_saved} placed, {n_skipped} skipped, {n_quit} unprocessed")
        print(f"Verify: python tools/roi_config.py --verify --name {args.name}")
        return 0

    # ── Incremental --field mode ─────────────────────────
    if args.field:
        if not output_path.exists():
            print(f"ERROR: {output_path} not found. --field requires an existing profile.")
            return 1
        with open(output_path, encoding="utf-8") as f:
            existing = json.load(f)
        print(f"Loaded existing profile {output_path.name}")
        print(f"Configuring only: {args.field}")

        # seat_N: collect ALL 6 sub-ROIs OR just one (with --element).
        # Existing seat with same seat_index is merged element-by-element: an
        # un-prompted element keeps its previous value, while a prompted-but-ESC'd
        # element is also preserved (NOT cleared) — multi-pass workflow friendly.
        if args.field.startswith("seat_"):
            idx = int(args.field.split("_")[1])

            # Find pre-existing entry for this seat_index, if any
            seats = existing.get("seats") or []
            prev_entry = None
            prev_idx_in_list = None
            for i, s in enumerate(seats):
                if s.get("seat_index") == idx:
                    prev_entry = s
                    prev_idx_in_list = i
                    break

            elements_to_prompt = [args.element] if args.element else SEAT_ELEMENT_ORDER

            print(f"\n{'=' * 56}")
            print(f"  SEAT {idx}: framing {len(elements_to_prompt)} element"
                  f"{'' if len(elements_to_prompt) == 1 else 's'}"
                  f" — un-prompted elements keep existing values")
            print(f"{'=' * 56}")

            captured = {}
            for elem in elements_to_prompt:
                print(f"\n▶ NOW FRAMING:  seat_{idx} → {elem.upper()}")
                print(f"  位置说明:    {ELEMENT_HINTS[elem]}")
                print(f"  操作:        鼠标拖框 → 按 SPACE 确认 / 按 ESC 跳过(保留旧值)")
                rect = select_roi(f"seat_{idx} — {elem}", img)
                captured[elem] = rect  # None if ESC

            # Build final entry merging captured + prev_entry
            # Schema keys in JSON differ slightly: action / fold_area / stack / button_indicator / cards / id
            seat_entry = dict(prev_entry) if prev_entry else {"seat_index": idx}
            for elem in elements_to_prompt:
                if captured[elem] is not None:
                    seat_entry[elem] = list(captured[elem])
                # else: keep whatever prev_entry had (or leave absent if new entry)

            # Replace existing by seat_index, else append
            if prev_idx_in_list is not None:
                seats[prev_idx_in_list] = seat_entry
                verb = "updated"
            else:
                seats.append(seat_entry)
                verb = "added"
            seats.sort(key=lambda s: s.get("seat_index", 0))
            existing["seats"] = seats
            cv2.destroyAllWindows()
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)

            configured = [k for k in SEAT_ELEMENT_ORDER if seat_entry.get(k)]
            missing = [e for e in REQUIRED_SEAT_ELEMENTS if not seat_entry.get(e)]
            print(f"\n✓ seat_{idx} {verb} in {output_path}")
            print(f"  configured: {configured}")
            if missing:
                print(f"  ⚠️  Pipeline 暂会忽略此 seat,直到补齐: {missing}")
                print(f"     Run --element <name> to fill them in (任何画面都可继续).")
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
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
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

    # Per-seat ROIs (full setup path — 6 elements per seat)
    seat_labels = _get_seat_labels(args.seats)
    for i, seat_name in enumerate(seat_labels):
        print(f"\n--- {seat_name} ---")
        rects = {}
        for elem in SEAT_ELEMENT_ORDER:
            print(f"\n▶ seat_{i} → {elem.upper()}   {ELEMENT_HINTS[elem]}")
            rects[elem] = select_roi(f"seat_{i} — {elem}", img)

        # Save only if action + stack present (required for pipeline load)
        if rects.get("action") and rects.get("stack"):
            entry = {"seat_index": i, "action": rects["action"], "stack": rects["stack"]}
            for opt in ("amount", "fold_area", "button_indicator", "cards", "id"):
                if rects.get(opt):
                    entry[opt] = rects[opt]
            result["seats"].append(entry)

    # ── Save ─────────────────────────────────────────────
    cv2.destroyAllWindows()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

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
        "fold": (0, 0, 255),       # red — emphasises fold_area distinct from action
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
        draw_rect(s.get("amount"), colors["pot"], "$" if s.get("amount") else "")
        draw_rect(s.get("fold_area"), colors["fold"], "FOLD" if s.get("fold_area") else "")
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
