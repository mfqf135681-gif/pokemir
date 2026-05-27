"""Phase 0 baseline 校准工具 — 用户人工标 30 个 action_events 验证 AI 准确率.

⚠️ 必须在 Win 桌面机跑(family.review/ 截图在那).Linux VPS 跑不到截图.

设计原则:
  • 抽样 stratified:20 个 low-conf (有截图) + 10 个 high-conf (无截图,提示用户)
  • cv2.imshow 显示截图;按数字键 1-5 选 action,s 跳过,q 退出
  • 输出 tools/output/baseline_<YYYYMMDD_HHMMSS>.csv + 控制台 summary

[[dev-rule-validate-blind-spots]] 风险声明:
  - cv2.imshow GUI 我无法 Linux 预验证 — Win 桌面机首跑时验证窗口能否显示
  - 若 imshow 失败 → 工具会显示截图路径,你可手动用文件管理器打开
  - 数字键 1-5 走 cv2.waitKey,Win 键盘布局可能差异 → 也支持终端 stdin 输入

用法:
    cd D:\\project\\pokemir
    .venv\\Scripts\\activate
    python tools/label_baseline.py
    python tools/label_baseline.py --n 30 --seed 42  # 可重现
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# Allow tool to be run from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REVIEW_DIR = Path("data/review")
OUTPUT_DIR = Path("tools/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ACTION_LABELS = {
    "1": "fold",
    "2": "check",
    "3": "call",
    "4": "raise",
    "5": "all_in",
}
ACTION_NAMES = {v: k for k, v in ACTION_LABELS.items()}


def _connect_db():
    """Returns sqlalchemy engine using POKEMIR_DB_DSN_SYNC env or Tailnet default."""
    try:
        from sqlalchemy import create_engine
    except ImportError:
        print("\n⚠️ sqlalchemy 没装,请先 `pip install sqlalchemy psycopg2-binary`", file=sys.stderr)
        sys.exit(1)
    dsn = os.getenv(
        "POKEMIR_DB_DSN_SYNC",
        "postgresql://poker_user:poker_pass@100.101.105.46:5432/poker_assistant",
    )
    return create_engine(dsn, future=True)


def sample_events(engine, n_low: int = 20, n_high: int = 10, seed: int | None = None):
    """Stratified sample: low-conf (有截图) + high-conf (无截图).

    抽样可重现:--seed 给定时,python random.sample 在 deterministic SQL 池上做选择.
    """
    from sqlalchemy import text
    if seed is not None:
        random.seed(seed)

    # 拉 deterministic 池(ORDER BY id 稳定,UUID 排序),python 再 random.sample
    sql_low = text("""
        SELECT ae.id::text AS event_id, ae.hand_id::text, ae.player_name,
               ae.position, ae.street, ae.action_type, ae.amount,
               ae.confidence_score, ae.raw_data, ae.timestamp
        FROM action_events ae
        WHERE ae.confidence_score < 0.7
        ORDER BY ae.id LIMIT 500
    """)
    sql_high = text("""
        SELECT ae.id::text AS event_id, ae.hand_id::text, ae.player_name,
               ae.position, ae.street, ae.action_type, ae.amount,
               ae.confidence_score, ae.raw_data, ae.timestamp
        FROM action_events ae
        WHERE ae.confidence_score >= 0.7
        ORDER BY ae.id LIMIT 500
    """)
    with engine.connect() as conn:
        low_pool = [dict(r._mapping) for r in conn.execute(sql_low).all()]
        high_pool = [dict(r._mapping) for r in conn.execute(sql_high).all()]

    low = random.sample(low_pool, min(n_low, len(low_pool)))
    high = random.sample(high_pool, min(n_high, len(high_pool)))
    events = low + high
    random.shuffle(events)
    return events


def find_screenshot(event_id: str, hand_id: str) -> Optional[Path]:
    """搜 data/review/<hand_id>/*_meta.json with matching event_id."""
    hand_dir = REVIEW_DIR / hand_id
    if not hand_dir.exists():
        return None
    for meta_file in hand_dir.glob("*_meta.json"):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if meta.get("event_id") == event_id:
                # Find sibling action.png (the most informative)
                prefix = meta_file.name.replace("_meta.json", "")
                for kind in ("action", "stack", "fold", "amount"):
                    img = hand_dir / f"{prefix}_{kind}.png"
                    if img.exists():
                        return img
        except Exception:
            continue
    return None


def render_event(event: dict, idx: int, total: int):
    """打印 event 详情 + 尝试显示截图."""
    print("\n" + "─" * 70)
    print(f"Event {idx + 1} / {total}  (event_id={event['event_id'][:8]}…)")
    print(f"Hand:    {event['hand_id'][:8]}…  Player: {event['player_name']}  Position: {event['position']}")
    print(f"Street:  {event['street']}  Timestamp: {event['timestamp']}")
    print(f"AI 记录: {event['action_type']}  amount={event['amount']}  conf={event['confidence_score']:.2f}")
    raw = event.get("raw_data") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    print(f"Stack:   {raw.get('stack_before')} → {raw.get('stack_after')}  delta={raw.get('stack_delta')}")
    print(f"Pot:     {raw.get('pot_before')} → {raw.get('pot_after')}  delta={raw.get('pot_delta')}")
    print(f"Text:    {raw.get('action_text')!r}")
    print(f"Override: {raw.get('override_reason') or '—'}")

    screenshot = find_screenshot(event["event_id"], event["hand_id"])
    if screenshot:
        print(f"📸 截图:  {screenshot}")
        try:
            import cv2
            img = cv2.imread(str(screenshot))
            if img is not None:
                # Resize if too small (action_area crops are tiny)
                h, w = img.shape[:2]
                if max(h, w) < 200:
                    scale = 200 / max(h, w)
                    img = cv2.resize(img, (int(w * scale), int(h * scale)),
                                     interpolation=cv2.INTER_NEAREST)
                cv2.imshow("baseline label", img)
                cv2.waitKey(1)  # Force window paint
        except Exception as e:
            print(f"⚠️ cv2.imshow 失败({e}) — 请手动打开上面的截图路径")
    else:
        print("📸 截图:  无(high-conf 不存)— 仅凭数字判断,可能存在循环验证偏差")


def get_user_input() -> str:
    """支持 cv2 keypress (1-5/s/q) 或 stdin fallback."""
    print("\n你判断真实 action:")
    print("  [1] fold    [2] check    [3] call    [4] raise    [5] all_in")
    print("  [s] 跳过   [q] 提前结束")
    while True:
        try:
            import cv2
            key = cv2.waitKey(0) & 0xFF
            ch = chr(key) if 0 < key < 128 else ""
            if ch in ("1", "2", "3", "4", "5", "s", "q"):
                return ch
        except Exception:
            pass
        # Fallback to stdin
        ch = input(">>> ").strip().lower()
        if ch in ("1", "2", "3", "4", "5", "s", "q"):
            return ch
        print("无效输入,请输 1-5 / s / q")


def save_csv(rows: list[dict], path: Path):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "event_id", "hand_id", "player_name", "street",
                "ai_action", "user_action", "agree", "has_screenshot",
                "confidence_score", "raw_text",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def report(rows: list[dict]):
    """打印 baseline summary."""
    print("\n" + "═" * 70)
    print("Baseline Report")
    print("═" * 70)

    judged = [r for r in rows if r["user_action"] not in ("skip", "quit")]
    skipped = [r for r in rows if r["user_action"] == "skip"]
    n = len(judged)
    if n == 0:
        print("⚠️ 全部跳过,无法计算准确率。")
        return

    agree = sum(1 for r in judged if r["agree"])
    print(f"\n📊 总样本: {len(rows)}  已判断: {n}  跳过: {len(skipped)}")
    print(f"   总体准确率: {agree}/{n} = {100 * agree / n:.1f}%")

    # With/without screenshot 分层
    with_ss = [r for r in judged if r["has_screenshot"]]
    without_ss = [r for r in judged if not r["has_screenshot"]]
    if with_ss:
        a = sum(1 for r in with_ss if r["agree"])
        print(f"\n   有截图(低 conf): {a}/{len(with_ss)} = {100 * a / len(with_ss):.1f}%  ← 高质量 baseline")
    if without_ss:
        a = sum(1 for r in without_ss if r["agree"])
        print(f"   无截图(高 conf): {a}/{len(without_ss)} = {100 * a / len(without_ss):.1f}%  ⚠️ 循环验证嫌疑")

    # By action_type
    print("\n📋 分 action_type:")
    by_ai = defaultdict(list)
    for r in judged:
        by_ai[r["ai_action"]].append(r)
    for action, items in sorted(by_ai.items()):
        a = sum(1 for r in items if r["agree"])
        print(f"   AI 标 {action:8s}: {a}/{len(items)} 用户确认 ({100*a/len(items):.0f}%)")

    # Disagreement patterns
    disagree = [(r["ai_action"], r["user_action"]) for r in judged if not r["agree"]]
    if disagree:
        print("\n🚨 不一致 pattern (top 5):")
        for (a, u), c in Counter(disagree).most_common(5):
            note = ""
            if (a, u) == ("check", "fold") or (a, u) == ("fold", "check"):
                note = "  ← T3 fold/check bug pattern"
            print(f"   AI={a:8s}  实际={u:8s}: {c} 次{note}")


def main():
    parser = argparse.ArgumentParser(description="Phase 0 baseline 校准工具")
    parser.add_argument("--n", type=int, default=30, help="总样本数(默认 30)")
    parser.add_argument("--n-low", type=int, default=20, help="低 conf 抽样(有截图)")
    parser.add_argument("--n-high", type=int, default=10, help="高 conf 抽样(无截图)")
    parser.add_argument("--seed", type=int, default=None, help="可重现的随机种子")
    args = parser.parse_args()

    print("Phase 0 Baseline 校准工具")
    print("═" * 70)
    print(f"将抽 {args.n_low} 个低 conf + {args.n_high} 个高 conf 事件让你标注。")
    print(f"低 conf 有截图;高 conf 仅数字。每个 1-2 分钟,总 {args.n_low + args.n_high} 个约 {(args.n_low + args.n_high) * 1.5:.0f} 分钟。\n")

    if not REVIEW_DIR.exists():
        print(f"⚠️ {REVIEW_DIR} 不存在!这意味着 pipeline 还没产生过低 conf 截图,或你不在 Win 桌面机。")
        ans = input("仍要继续吗?(高 conf 事件没截图,但能从数字标)[y/N]: ").strip().lower()
        if ans != "y":
            sys.exit(0)

    engine = _connect_db()
    print("连接 DB,抽样...")
    events = sample_events(engine, n_low=args.n_low, n_high=args.n_high, seed=args.seed)
    print(f"抽到 {len(events)} 个 events。开始标注。\n")

    rows = []
    quit_early = False
    for idx, event in enumerate(events):
        if quit_early:
            rows.append({
                "event_id": event["event_id"], "hand_id": event["hand_id"],
                "player_name": event["player_name"], "street": event["street"],
                "ai_action": event["action_type"], "user_action": "quit",
                "agree": False, "has_screenshot": False,
                "confidence_score": event["confidence_score"],
                "raw_text": (event.get("raw_data") or {}).get("action_text", "") if isinstance(event.get("raw_data"), dict) else "",
            })
            continue

        render_event(event, idx, len(events))
        screenshot = find_screenshot(event["event_id"], event["hand_id"])
        choice = get_user_input()
        if choice == "q":
            print("\n用户提前结束,保存已标注。")
            quit_early = True
            user_action = "quit"
            agree = False
        elif choice == "s":
            user_action = "skip"
            agree = False
        else:
            user_action = ACTION_LABELS[choice]
            agree = (user_action == event["action_type"])
            ai_action = event["action_type"]
            marker = "  ✅" if agree else f"  ❌ (AI: {ai_action})"
            print(f"   你: {user_action}{marker}")
        raw = event.get("raw_data") or {}
        if isinstance(raw, str):
            try: raw = json.loads(raw)
            except Exception: raw = {}
        rows.append({
            "event_id": event["event_id"], "hand_id": event["hand_id"],
            "player_name": event["player_name"], "street": event["street"],
            "ai_action": event["action_type"], "user_action": user_action,
            "agree": agree, "has_screenshot": screenshot is not None,
            "confidence_score": event["confidence_score"],
            "raw_text": raw.get("action_text", ""),
        })

    # Cleanup cv2
    try:
        import cv2
        cv2.destroyAllWindows()
    except Exception:
        pass

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"baseline_{ts}.csv"
    save_csv(rows, csv_path)
    print(f"\n💾 已保存: {csv_path}")
    report(rows)


if __name__ == "__main__":
    main()
