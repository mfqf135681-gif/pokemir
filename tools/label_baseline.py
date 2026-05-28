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
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env so POKEMIR_DB_DSN_SYNC picks up user's real password (project-wide pattern)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # graceful: env var may already be set in shell

REVIEW_DIR = PROJECT_ROOT / "data" / "review"   # 绝对路径,不依赖 CWD
OUTPUT_DIR = PROJECT_ROOT / "tools" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ACTION_LABELS = {
    "1": "fold",
    "2": "check",
    "3": "call",
    "4": "bet",      # 首次主动下注(街开始 to_call=0)
    "5": "raise",    # 已有下注基础上加大
    "6": "all_in",
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


def sample_events(engine, n_screenshot: int = 30, n_override: int = 5, n_clean: int = 5, seed: int | None = None):
    """3 池抽样 — 优先有截图的 ground truth.

    池子定义:
    1. **screenshot 池** = confidence_score < 0.7 → pipeline 必存了 data/review/ 截图
    2. **override 池** = override 触发(conf=0.7,**无截图** — 凭 Text 字段判断)
    3. **clean 池** = conf ≥ 0.7 且未 override(纯 text 高 conf,**无截图** — 抽查防漂移)

    默认 30+5+5,看图为主,凭 Text 抽查为辅。
    """
    from sqlalchemy import text
    if seed is not None:
        random.seed(seed)

    sql_screenshot = text("""
        SELECT ae.id::text AS event_id, ae.hand_id::text, ae.player_name,
               ae.position, ae.street, ae.action_type, ae.amount,
               ae.confidence_score, ae.raw_data, ae.timestamp
        FROM action_events ae
        WHERE ae.confidence_score < 0.7
        ORDER BY ae.id LIMIT 1000
    """)
    sql_override = text("""
        SELECT ae.id::text AS event_id, ae.hand_id::text, ae.player_name,
               ae.position, ae.street, ae.action_type, ae.amount,
               ae.confidence_score, ae.raw_data, ae.timestamp
        FROM action_events ae
        WHERE ae.raw_data->>'override_reason' IS NOT NULL
          AND ae.confidence_score >= 0.7
        ORDER BY ae.id LIMIT 1000
    """)
    sql_clean = text("""
        SELECT ae.id::text AS event_id, ae.hand_id::text, ae.player_name,
               ae.position, ae.street, ae.action_type, ae.amount,
               ae.confidence_score, ae.raw_data, ae.timestamp
        FROM action_events ae
        WHERE ae.confidence_score >= 0.7
          AND ae.raw_data->>'override_reason' IS NULL
        ORDER BY ae.id LIMIT 1000
    """)
    with engine.connect() as conn:
        screenshot_pool = [dict(r._mapping) for r in conn.execute(sql_screenshot).all()]
        override_pool = [dict(r._mapping) for r in conn.execute(sql_override).all()]
        clean_pool = [dict(r._mapping) for r in conn.execute(sql_clean).all()]

    screenshot = random.sample(screenshot_pool, min(n_screenshot, len(screenshot_pool)))
    override = random.sample(override_pool, min(n_override, len(override_pool)))
    clean = random.sample(clean_pool, min(n_clean, len(clean_pool)))
    print(f"   📸 有截图池(conf<0.7): {len(screenshot_pool)} 个 → 抽 {len(screenshot)} 个 ← 主力")
    print(f"   📋 override 池(conf=0.7,无图): {len(override_pool)} 个 → 抽 {len(override)} 个")
    print(f"   ✨ 纯 text 高 conf 抽查(无图): {len(clean_pool)} 个 → 抽 {len(clean)} 个")
    events = screenshot + override + clean
    random.shuffle(events)
    return events


def find_screenshot(event_id: str, hand_id: str, verbose: bool = False) -> Optional[Path]:
    """搜 data/review/<hand_id>/*_meta.json with matching event_id。
    verbose=True 时打印诊断信息,便于定位 find 失败原因。
    """
    hand_dir = REVIEW_DIR / hand_id
    if not hand_dir.exists():
        if verbose:
            print(f"   [find_ss] 目录不存在: {hand_dir}")
        return None
    meta_files = list(hand_dir.glob("*_meta.json"))
    if verbose:
        print(f"   [find_ss] hand_dir={hand_dir},找到 {len(meta_files)} 个 meta.json")
    for meta_file in meta_files:
        try:
            # T23 (2026-05-28):历史 meta.json 是 cp936 写的(orchestrator
            # 旧版没 encoding=),utf-8 读失败 fallback cp936
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                meta = json.loads(meta_file.read_text(encoding="cp936"))
                if verbose:
                    print(f"   [find_ss] {meta_file.name} 用 cp936 兼容读")
            meta_eid = meta.get("event_id")
            if meta_eid == event_id:
                prefix = meta_file.name.replace("_meta.json", "")
                if verbose:
                    print(f"   [find_ss] 匹配到 meta:{meta_file.name},prefix={prefix}")
                for kind in ("action", "stack", "fold", "amount"):
                    img = hand_dir / f"{prefix}_{kind}.png"
                    if img.exists():
                        if verbose:
                            print(f"   [find_ss] 找到截图:{img.name}")
                        return img
                if verbose:
                    print(f"   [find_ss] meta 匹配但找不到 .png(prefix_kind 都不在)")
            elif verbose:
                print(f"   [find_ss] meta event_id 不匹配:{meta_eid} != {event_id}")
        except Exception as e:
            if verbose:
                print(f"   [find_ss] 读 meta 失败 {meta_file.name}: {e}")
            continue
    return None


def open_image_native(image_path: Path) -> bool:
    """用系统默认图片查看器打开(cv2.imshow 失败时 fallback)。
    Windows: os.startfile;Linux: xdg-open;macOS: open。
    """
    try:
        import sys, subprocess
        if sys.platform == "win32":
            os.startfile(str(image_path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(image_path)], check=False)
        else:
            subprocess.run(["xdg-open", str(image_path)], check=False)
        return True
    except Exception as e:
        print(f"   ⚠️ 系统查看器打开失败:{e}")
        return False


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

    screenshot = find_screenshot(event["event_id"], event["hand_id"], verbose=True)
    if screenshot:
        print(f"📸 截图:  {screenshot}")
        cv2_ok = False
        try:
            import cv2
            img = cv2.imread(str(screenshot))
            if img is not None:
                # Resize if too small (action_area crops are tiny)
                h, w = img.shape[:2]
                if max(h, w) < 200:
                    scale = 400 / max(h, w)  # 放大到 400px,看得清
                    img = cv2.resize(img, (int(w * scale), int(h * scale)),
                                     interpolation=cv2.INTER_NEAREST)
                cv2.imshow("baseline label - press any key in this window to focus", img)
                cv2.waitKey(1)
                cv2_ok = True
        except Exception as e:
            print(f"   ⚠️ cv2.imshow 失败({e})")
        # Fallback:OS-native viewer(Windows Photos / Linux eog / macOS Preview)
        if not cv2_ok:
            print(f"   📂 用系统查看器打开...")
            open_image_native(screenshot)
    else:
        print("📸 截图:  无 — 凭 Text 字段判断(注意:stack=null 是 OCR 漏读,≠ 玩家没投钱)")


def get_user_input() -> str:
    """支持 cv2 keypress (1-6/s/q) 或 stdin fallback."""
    print("\n你判断真实 action:")
    print("  [1] fold    [2] check   [3] call")
    print("  [4] bet     [5] raise   [6] all_in")
    print("  [s] 跳过   [q] 提前结束")
    while True:
        try:
            import cv2
            key = cv2.waitKey(0) & 0xFF
            ch = chr(key) if 0 < key < 128 else ""
            if ch in ("1", "2", "3", "4", "5", "6", "s", "q"):
                return ch
        except Exception:
            pass
        # Fallback to stdin
        ch = input(">>> ").strip().lower()
        if ch in ("1", "2", "3", "4", "5", "6", "s", "q"):
            return ch
        print("无效输入,请输 1-6 / s / q")


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
        print(f"\n   有截图: {a}/{len(with_ss)} = {100 * a / len(with_ss):.1f}%  ← 高质量(看图判断)")
    if without_ss:
        a = sum(1 for r in without_ss if r["agree"])
        print(f"   无截图: {a}/{len(without_ss)} = {100 * a / len(without_ss):.1f}%  ⚠️ 凭 Text 字段判断")

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
    parser = argparse.ArgumentParser(description="Phase 0 baseline 校准工具 (3 池抽样 — 截图为主)")
    parser.add_argument("--n-screenshot", type=int, default=30, help="有截图主力(conf<0.7,看图标注)")
    parser.add_argument("--n-override", type=int, default=5, help="override 触发抽样(无截图,凭 Text 判断)")
    parser.add_argument("--n-clean", type=int, default=5, help="高 conf 抽查(无截图,防沉默错误漂)")
    parser.add_argument("--seed", type=int, default=None, help="可重现的随机种子")
    args = parser.parse_args()
    total = args.n_screenshot + args.n_override + args.n_clean

    print("Phase 0 Baseline 校准工具 (3 池抽样 — 截图为主)")
    print("═" * 70)
    print(f"📸 有截图主力 {args.n_screenshot} 个(conf<0.7,看图判断 ground truth)")
    print(f"📋 override 抽样 {args.n_override} 个(无截图,凭 Text)")
    print(f"✨ 高 conf 抽查 {args.n_clean} 个(无截图,凭 Text 防漂)")
    print(f"总 {total} 个,每个 1-2 分钟,约 {total * 1.5:.0f} 分钟。\n")

    if not REVIEW_DIR.exists():
        print(f"⚠️ {REVIEW_DIR} 不存在!这意味着 pipeline 还没产生过低 conf 截图,或你不在 Win 桌面机。")
        ans = input("仍要继续吗?(高 conf 事件没截图,但能从数字标)[y/N]: ").strip().lower()
        if ans != "y":
            sys.exit(0)

    engine = _connect_db()
    print("连接 DB,抽样...")
    events = sample_events(engine, n_screenshot=args.n_screenshot, n_override=args.n_override, n_clean=args.n_clean, seed=args.seed)
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
