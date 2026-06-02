"""tools/compare_truth.py — 真值 vs DB 捕获率比对(T126 步骤③ 框架)

把**人工标注的真值**(看回放逐手记的真实动作)与 **DB 里 pipeline 实际抓到的 action_events**
对比,算「筹码动作捕获率」+ 列漏抓(missed)/多抓(extra,假阳)。
这是回答"到没到 95%"的唯一诚实标尺(见 doc §8 #1)。

⚠️ 框架版(v0):
  - 解析(parse_labels)+ 比对(compare_hand)是**纯函数,已在 --self-test 验**。
  - 匹配启发式(同 street + 同位置 + 同类型)**见到真实数据后必调**(amount 容差、位置漂移等)。
  - DB 部分(fetch_db_actions)需**连库**跑(Win / 有 DSN 的环境);Linux 无库用 --self-test。
  - 手牌对齐(标注的手 ↔ DB 的手)v0 只支持 hand_id(UUID);按时间窗对齐留 TODO。

标注格式(纯文本,好手写;见文末 TEMPLATE):
  # hand <hand_id 或 录制内序号>
  preflop: UTG 弃, MP 跟 2, CO 加 6, BB 跟 4
  flop: BB 过, MP 下注 8, BB 弃
  # hand ...

用法:
  python tools/compare_truth.py --self-test            # 不连库,验解析+比对
  python tools/compare_truth.py --labels truth.txt     # 连库,逐手比对(需 hand_id)
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field

# 让 `python tools/compare_truth.py` 能 import 项目根的 storage/config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 动作词归一(中/英 → ActionType 值)──────────────────────────────
_ACT = {
    "弃": "fold", "弃牌": "fold", "fold": "fold",
    "跟": "call", "跟注": "call", "call": "call",
    "加": "raise", "加注": "raise", "raise": "raise",
    "下": "bet", "下注": "bet", "bet": "bet",
    "过": "check", "过牌": "check", "check": "check",
    "全下": "all_in", "allin": "all_in", "all_in": "all_in",
    "盲注小": "post_sb", "盲注大": "post_bb",
}
CHIP_ACTIONS = {"call", "bet", "raise", "all_in"}   # 筹码动作(目标度量;fold/check 不算)
STREETS = {"preflop", "flop", "turn", "river", "showdown"}


@dataclass
class Action:
    street: str
    pos: str            # 位置或座位标识(SB/BB/UTG/...或座位号)
    type: str           # 归一后的 ActionType 值
    amount: float | None = None
    raw: str = ""


@dataclass
class LabeledHand:
    key: str                          # hand_id(UUID)或录制内序号
    actions: list[Action] = field(default_factory=list)

    def chip_actions(self) -> list[Action]:
        return [a for a in self.actions if a.type in CHIP_ACTIONS]


def _parse_action(street: str, token: str) -> Action | None:
    """'CO 加 6' / 'MP call 2' / 'UTG 弃' → Action."""
    token = token.strip()
    if not token:
        return None
    parts = token.split()
    if len(parts) < 2:
        return None
    pos = parts[0]
    # 动作词可能是 parts[1];金额(若有)是末尾的数字
    word = parts[1]
    atype = _ACT.get(word)
    if atype is None:
        # 容错:整段里找已知动作词
        for w, a in _ACT.items():
            if w in token:
                atype = a
                break
    if atype is None:
        return None
    amount = None
    m = re.search(r"(\d+(?:\.\d+)?)", token)
    if m:
        amount = float(m.group(1))
    return Action(street=street, pos=pos, type=atype, amount=amount, raw=token)


def parse_labels(text: str) -> list[LabeledHand]:
    """解析标注文本 → [LabeledHand]。"""
    hands: list[LabeledHand] = []
    cur: LabeledHand | None = None
    for ln in text.splitlines():
        line = ln.strip()
        if not line:
            continue
        if line.startswith("# hand"):
            key = line[len("# hand"):].strip() or f"hand_{len(hands)+1}"
            cur = LabeledHand(key=key)
            hands.append(cur)
            continue
        if line.startswith("#"):
            continue  # 普通注释
        if cur is None:
            continue
        if ":" not in line:
            continue
        street, rest = line.split(":", 1)
        street = street.strip().lower()
        if street not in STREETS:
            continue
        for tok in rest.split(","):
            act = _parse_action(street, tok)
            if act:
                cur.actions.append(act)
    return hands


# ── 比对 ────────────────────────────────────────────────────────────
@dataclass
class CompareResult:
    matched: int = 0
    missed: list[Action] = field(default_factory=list)    # 真值有,DB 无 = 漏抓
    extra: list[dict] = field(default_factory=list)        # DB 有,真值无 = 多抓/假阳

    @property
    def total_true(self) -> int:
        return self.matched + len(self.missed)

    @property
    def capture_rate(self) -> float:
        return self.matched / self.total_true if self.total_true else float("nan")


def compare_hand(true_actions: list[Action], db_actions: list[dict]) -> CompareResult:
    """真值筹码动作 vs DB 筹码动作。v0 匹配键 = (street, pos, type),贪心一对一。
    db_actions: [{'street','pos','type','amount'}]  (由 fetch_db_actions 产)
    ⚠️ 匹配启发式见真实数据后调(amount 容差 / 位置漂移 / 同街多个同类型动作的配对)。"""
    res = CompareResult()
    db_pool = [d for d in db_actions if d.get("type") in CHIP_ACTIONS]
    used = [False] * len(db_pool)
    for ta in true_actions:
        if ta.type not in CHIP_ACTIONS:
            continue
        hit = -1
        for j, d in enumerate(db_pool):
            if used[j]:
                continue
            if d.get("street") == ta.street and d.get("pos") == ta.pos and d.get("type") == ta.type:
                hit = j
                break
        if hit >= 0:
            used[hit] = True
            res.matched += 1
        else:
            res.missed.append(ta)
    res.extra = [db_pool[j] for j in range(len(db_pool)) if not used[j]]
    return res


# ── DB 取数(需连库)─────────────────────────────────────────────────
def fetch_db_actions(hand_key: str) -> list[dict]:
    """按 hand_id(UUID)从 DB 取该手 action_events → [{street,pos,type,amount}].
    ⚠️ 仅支持 hand_key 是 UUID;按时间窗对齐留 TODO(用 manifest t_wall ↔ hands.started_at)。"""
    from uuid import UUID
    try:
        from storage.database import SessionLocal
        from storage.models import ActionEventModel
    except Exception as e:
        raise RuntimeError(f"连库依赖缺失/导入失败:{e}")
    try:
        hid = UUID(hand_key)
    except ValueError:
        raise NotImplementedError(
            f"hand_key '{hand_key}' 不是 UUID。按时间窗对齐还没实现(TODO):"
            f"用 manifest 的 t_wall 找 hands.started_at 最近的手。")
    sess = SessionLocal()
    try:
        rows = (sess.query(ActionEventModel)
                .filter(ActionEventModel.hand_id == hid)
                .order_by(ActionEventModel.sequence_number).all())
        return [{"street": r.street, "pos": r.position, "type": r.action_type,
                 "amount": r.amount} for r in rows]
    finally:
        sess.close()


def _report(hand_key: str, res: CompareResult):
    print(f"\n=== hand {hand_key} ===")
    print(f"  真值筹码动作 {res.total_true} | 抓到 {res.matched} | "
          f"捕获率 {res.capture_rate*100:.0f}%" if res.total_true else "  (无筹码动作)")
    if res.missed:
        print(f"  漏抓 {len(res.missed)}:")
        for a in res.missed:
            print(f"    - {a.street} {a.pos} {a.type} {a.amount or ''}".rstrip())
    if res.extra:
        print(f"  多抓/假阳 {len(res.extra)}:")
        for d in res.extra:
            print(f"    + {d.get('street')} {d.get('pos')} {d.get('type')} {d.get('amount') or ''}".rstrip())


# ── self-test(纯逻辑,不连库)────────────────────────────────────────
def _self_test():
    sample = """
# hand demo1
preflop: UTG 弃, MP 跟 2, CO 加 6, BB 跟 4
flop: BB 过, MP 下注 8, BB 弃
"""
    hands = parse_labels(sample)
    assert len(hands) == 1, hands
    h = hands[0]
    chips = h.chip_actions()
    # 筹码动作:MP call2, CO raise6, BB call4, MP bet8  = 4(弃/过不算)
    assert len(chips) == 4, [(a.pos, a.type, a.amount) for a in h.actions]
    assert chips[0].type == "call" and chips[0].amount == 2
    assert chips[1].type == "raise" and chips[1].amount == 6

    # 模拟 DB:抓到了 CO raise + BB call,漏了 MP call2 和 MP bet8,且多抓一个 SB call
    db = [
        {"street": "preflop", "pos": "CO", "type": "raise", "amount": 6},
        {"street": "preflop", "pos": "BB", "type": "call", "amount": 4},
        {"street": "preflop", "pos": "SB", "type": "call", "amount": 1},  # 假阳
    ]
    res = compare_hand(h.actions, db)
    assert res.matched == 2, res.matched
    assert len(res.missed) == 2, res.missed       # MP call2, MP bet8
    assert len(res.extra) == 1, res.extra          # SB call
    assert abs(res.capture_rate - 0.5) < 1e-9, res.capture_rate
    _report("demo1", res)
    print("\n✅ self-test 通过:解析 + 比对逻辑 OK(匹配启发式待真实数据调)")


def main():
    ap = argparse.ArgumentParser(description="真值 vs DB 捕获率比对(T126 框架)")
    ap.add_argument("--labels", type=str, help="标注文本文件路径")
    ap.add_argument("--self-test", action="store_true", help="不连库,跑内置样例验解析+比对")
    args = ap.parse_args()

    if args.self_test:
        _self_test()
        return
    if not args.labels:
        ap.error("给 --labels <文件> 或 --self-test")

    text = open(args.labels, encoding="utf-8").read()
    hands = parse_labels(text)
    print(f"解析到 {len(hands)} 手。")
    agg_true = agg_match = 0
    for h in hands:
        try:
            db = fetch_db_actions(h.key)
        except (RuntimeError, NotImplementedError) as e:
            print(f"  [跳过 hand {h.key}] {e}")
            continue
        res = compare_hand(h.actions, db)
        _report(h.key, res)
        agg_true += res.total_true
        agg_match += res.matched
    if agg_true:
        print(f"\n=== 汇总 ===\n  总真值筹码动作 {agg_true} | 抓到 {agg_match} | "
              f"整体捕获率 {agg_match/agg_true*100:.1f}%")


if __name__ == "__main__":
    main()

# ── 标注 TEMPLATE(复制改用)──────────────────────────────────────────
# # hand <DB的hand_id(UUID) 或 录制内第几手>
# preflop: UTG 弃, UTG+1 跟 2, MP 弃, CO 加 6, BTN 弃, SB 弃, BB 跟 4
# flop: BB 过, CO 下注 8, BB 跟 8
# turn: BB 过, CO 过
# river: BB 下注 20, CO 弃
# # hand ...
#   动作词:弃/跟/加/下注/过/全下(也认 fold/call/raise/bet/check/all_in)
#   金额跟在动作后(下注/加注/跟注 记数字;弃/过 不用)
#   位置:SB BB UTG UTG+1 MP MP+1 HJ CO BTN(或你习惯的座位号,与 DB 对齐即可)
