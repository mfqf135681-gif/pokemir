"""tools/truth_score.py — 阶段-1 真值地基:评分对账 harness(系统外尺子)。

设计 doc: requirement-discussions/主题-主程序架构重构.md →《执行排程》阶段-1 + 4 铁律③
("一切'变好'对真值量、不对自身量")。这把尺子闭掉本会话开场的【圆形验证】担忧:
管线的"准确率"不再自报(守恒/求解器自洽=内部循环),而是对**人工标注的真值**量。

用途:
  - 把人工标的真值(逐手:座位栈/动作序/赢家/盲注)对 DB 里管线落的结果对账;
  - 输出 动作召回·精度 / 端点栈误差 / 赢家正确率 / 守恒;
  - 阶段0(干净窗 A/B)、阶段1(求解器真捕获率)、阶段3(重构正确性不回归)共用此尺。

架构(同 replay_reconstruct 套路):**可测纯核**(normalize / score_hand / match_hands /
aggregate,Linux 全可单测)+ **隔离的 DB 适配器**(fetch_db_hands,需 psycopg2,guard 导入)。

真值 JSON 格式见 tools/truth_template.json。真值与 DB 都归一化到同一 NormHand 形状再比。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

# 从 tools/ 直接运行(python tools\truth_score.py)时,默认只有 tools/ 在 sys.path,
# import config 会失败 → 自动连库拿不到 DB_DSN_SYNC。把项目根加进 path 修正。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 自愿动作(强制注 post_* 默认不计入召回/精度,单列)
VOLUNTARY = {"fold", "check", "call", "bet", "raise", "all_in"}
STREETS = ("preflop", "flop", "turn", "river")


# ── 归一化形状 ────────────────────────────────────────────
@dataclass
class NormHand:
    """真值与 DB 输出共用的归一化一手。seat 一律 int 键。"""
    seats: dict          # {seat: {"name": str|None, "initial": float|None, "final": float|None}}
    actions: list        # [{"seat": int, "street": str, "action": str, "amount": float|None}]
    winners: list        # [seat:int]
    button_seat: int | None = None
    blinds: dict = field(default_factory=dict)   # {"sb","bb","ante"}


def amount_match(a: float | None, b: float | None, abs_tol: float = 2.0,
                 rel_tol: float = 0.05) -> bool:
    """金额匹配:两者皆 None→True(check/fold);一 None 一有值→False;
    否则 |a-b| ≤ max(abs_tol, rel_tol·max(|a|,|b|))。"""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))


# ── 可测纯核:逐手评分 ────────────────────────────────────
def score_hand(truth: NormHand, db: NormHand,
               abs_tol: float = 2.0, rel_tol: float = 0.05) -> dict:
    """对账一手 truth vs db。返回逐项指标 dict(不做聚合)。"""
    # --- 动作召回/精度(贪心双向匹配,仅自愿动作)---
    t_acts = [a for a in truth.actions if a["action"] in VOLUNTARY]
    d_acts = [a for a in db.actions if a["action"] in VOLUNTARY]
    used_db = set()
    matched = 0
    misamount = 0   # 动作对上但金额不符(seat/street/action 同、amount 偏)
    for ta in t_acts:
        for j, da in enumerate(d_acts):
            if j in used_db:
                continue
            if (ta["seat"] == da["seat"] and ta["street"] == da["street"]
                    and ta["action"] == da["action"]):
                if amount_match(ta.get("amount"), da.get("amount"), abs_tol, rel_tol):
                    used_db.add(j)
                    matched += 1
                    break
        else:
            # 没找到金额也对的;再找一个 seat/street/action 对但金额不符的,算"漏额"
            for j, da in enumerate(d_acts):
                if j in used_db:
                    continue
                if (ta["seat"] == da["seat"] and ta["street"] == da["street"]
                        and ta["action"] == da["action"]):
                    used_db.add(j)
                    misamount += 1
                    break
    recall = matched / len(t_acts) if t_acts else None
    precision = matched / len(d_acts) if d_acts else None

    # --- 端点栈误差(per-seat initial/final)---
    def _stack_errs(key: str):
        errs, within = [], 0
        n = 0
        for s, tv in truth.seats.items():
            tval = tv.get(key)
            if tval is None:
                continue
            n += 1
            dval = (db.seats.get(s) or {}).get(key)
            if dval is None:
                errs.append(None)   # DB 缺座
                continue
            e = abs(dval - tval)
            errs.append(e)
            if e <= max(abs_tol, rel_tol * abs(tval)):
                within += 1
        present = [e for e in errs if e is not None]
        return {
            "n": n,
            "covered": len(present),
            "missing": n - len(present),
            "within_tol": within,
            "max_err": max(present) if present else None,
            "mean_err": (sum(present) / len(present)) if present else None,
        }

    # --- 赢家正确率 ---
    tw, dw = set(truth.winners), set(db.winners)
    winner_exact = (tw == dw)
    inter = tw & dw
    jacc = (len(inter) / len(tw | dw)) if (tw or dw) else None

    return {
        "action_recall": recall,
        "action_precision": precision,
        "action_matched": matched,
        "action_misamount": misamount,
        "action_truth_n": len(t_acts),
        "action_db_n": len(d_acts),
        "initial_stack": _stack_errs("initial"),
        "final_stack": _stack_errs("final"),
        "winner_exact": winner_exact,
        "winner_jaccard": jacc,
        "winner_truth": sorted(tw),
        "winner_db": sorted(dw),
    }


def match_hands(truth: list[NormHand], db: list[NormHand]) -> list[dict]:
    """按【顺序】对齐(真值与 DB 同一场次按时间序),逐对带一致性 flag。
    顺序对齐是务实首版:真值与 DB 都按时间序 → zip;button_seat/盲注不一致 → flag 标错位。
    返回 [{"i","truth","db","consistency_flags":[...]}],长度 = min(len)。错位需人工核。"""
    pairs = []
    n = min(len(truth), len(db))
    for i in range(n):
        t, d = truth[i], db[i]
        flags = []
        if t.button_seat is not None and d.button_seat is not None \
                and t.button_seat != d.button_seat:
            flags.append(f"button_mismatch(t={t.button_seat},db={d.button_seat})")
        for k in ("sb", "bb", "ante"):
            tv, dv = t.blinds.get(k), d.blinds.get(k)
            if tv is not None and dv is not None and tv != dv:
                flags.append(f"{k}_mismatch(t={tv},db={dv})")
        pairs.append({"i": i, "truth": t, "db": d, "consistency_flags": flags})
    if len(truth) != len(db):
        pairs.append({"i": -1, "truth": None, "db": None,
                      "consistency_flags": [f"COUNT_MISMATCH(truth={len(truth)},db={len(db)})"]})
    return pairs


def aggregate(scores: list[dict]) -> dict:
    """聚合多手指标 → 总览(召回/精度均值、端点覆盖与误差、赢家正确率)。"""
    def _avg(xs):
        xs = [x for x in xs if x is not None]
        return (sum(xs) / len(xs)) if xs else None

    n = len(scores)
    return {
        "hands": n,
        "action_recall_avg": _avg([s["action_recall"] for s in scores]),
        "action_precision_avg": _avg([s["action_precision"] for s in scores]),
        "action_misamount_total": sum(s["action_misamount"] for s in scores),
        "final_within_tol_rate": _avg([
            (s["final_stack"]["within_tol"] / s["final_stack"]["n"])
            for s in scores if s["final_stack"]["n"]
        ]),
        "final_missing_total": sum(s["final_stack"]["missing"] for s in scores),
        "initial_within_tol_rate": _avg([
            (s["initial_stack"]["within_tol"] / s["initial_stack"]["n"])
            for s in scores if s["initial_stack"]["n"]
        ]),
        "winner_exact_rate": _avg([1.0 if s["winner_exact"] else 0.0 for s in scores]),
    }


# ── 真值加载 + 归一化 ─────────────────────────────────────
def truth_to_norm(h: dict) -> NormHand:
    """真值 JSON 一手 → NormHand。seat 键转 int。"""
    seats = {int(s): {"name": v.get("name"),
                      "initial": v.get("initial"), "final": v.get("final")}
             for s, v in (h.get("seats") or {}).items()}
    actions = [{"seat": int(a["seat"]), "street": a["street"],
                "action": a["action"], "amount": a.get("amount")}
               for a in (h.get("actions") or [])]
    winners = [int(w["seat"]) if isinstance(w, dict) else int(w)
               for w in (h.get("winners") or [])]
    return NormHand(seats=seats, actions=actions, winners=winners,
                    button_seat=h.get("button_seat"), blinds=h.get("blinds") or {})


def load_truth(path: str) -> list[NormHand]:
    data = json.loads(open(path, encoding="utf-8").read())
    return [truth_to_norm(h) for h in data.get("hands", [])]


# ── 隔离的 DB 适配器(Win/有库时用;纯核不依赖它)──────────
def db_row_to_norm(hand_row: dict, action_rows: list[dict]) -> NormHand:
    """DB 一手(hands 行 + 其 action_events 行)→ NormHand。
    hand_row: {raw_data, result, ...};action_rows: [{raw_data, action_type, amount, street}]。"""
    rd = hand_row.get("raw_data") or {}
    init = {int(k): v for k, v in (rd.get("player_stacks_initial") or {}).items()}
    fin = {int(k): v for k, v in (rd.get("player_stacks_final") or {}).items()}
    names = {int(k): v for k, v in (rd.get("seat_names") or {}).items()}
    seats = {}
    for s in set(init) | set(fin) | set(names):
        seats[s] = {"name": names.get(s), "initial": init.get(s), "final": fin.get(s)}
    actions = []
    for a in action_rows:
        ard = a.get("raw_data") or {}
        si = ard.get("seat_index")
        if si is None:
            continue
        actions.append({"seat": int(si), "street": a.get("street"),
                        "action": a.get("action_type"), "amount": a.get("amount")})
    result = hand_row.get("result") or {}
    winners = result.get("winners_endpoint") or result.get("win_seats_xx") or []
    bl = rd.get("blind_level") or {}
    return NormHand(seats=seats, actions=actions, winners=[int(w) for w in winners],
                    button_seat=rd.get("button_seat_index"), blinds=bl)


def fetch_db_hands(conn_str: str, started_after: str, started_before: str) -> list[NormHand]:
    """从 PG 拉时间区间内的手(按 started_at 序)→ [NormHand]。需 psycopg2(guard 导入)。
    conn_str 可直接传 SQLAlchemy DSN(自动剥 +psycopg2/+asyncpg 给 psycopg2 用)。"""
    import psycopg2  # noqa: 仅此函数需要;纯核与单测不触
    import psycopg2.extras
    dsn = conn_str
    for tag in ("+psycopg2", "+asyncpg", "+psycopg"):
        dsn = dsn.replace(tag, "")
    out = []
    with psycopg2.connect(dsn) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, started_at, result, raw_data FROM hands "
                "WHERE started_at >= %s AND started_at <= %s ORDER BY started_at",
                (started_after, started_before))
            hands = cur.fetchall()
            for h in hands:
                cur.execute(
                    "SELECT raw_data, action_type, amount, street FROM action_events "
                    "WHERE hand_id = %s ORDER BY sequence_number", (h["id"],))
                acts = cur.fetchall()
                out.append(db_row_to_norm(dict(h), [dict(a) for a in acts]))
    return out


def _print_report(pairs, scores, agg):
    print("=" * 60)
    print(f"真值对账:{agg['hands']} 手")
    print(f"  动作召回 avg={agg['action_recall_avg']}  精度 avg={agg['action_precision_avg']}"
          f"  金额偏差笔数={agg['action_misamount_total']}")
    print(f"  端点 final 命中率={agg['final_within_tol_rate']}  缺座={agg['final_missing_total']}")
    print(f"  端点 initial 命中率={agg['initial_within_tol_rate']}")
    print(f"  赢家完全正确率={agg['winner_exact_rate']}")
    bad = [p for p in pairs if p.get("consistency_flags")]
    if bad:
        print("  ⚠️ 对齐一致性 flag(可能错位,需人工核):")
        for p in bad:
            print(f"    手#{p['i']}: {p['consistency_flags']}")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="真值对账 harness")
    ap.add_argument("--truth", required=True, help="真值 JSON(见 truth_template.json)")
    ap.add_argument("--conn", help="PG 连接串(给则拉 DB 对账)")
    ap.add_argument("--after", help="started_at 下界(ISO)")
    ap.add_argument("--before", help="started_at 上界(ISO)")
    ap.add_argument("--abs-tol", type=float, default=2.0)
    ap.add_argument("--rel-tol", type=float, default=0.05)
    args = ap.parse_args(argv)

    truth = load_truth(args.truth)
    conn = args.conn
    if not conn:
        try:
            from config import DB_DSN_SYNC
            conn = DB_DSN_SYNC   # 不给 --conn 时自动用项目库(免手填凭据)
        except Exception:
            pass
    if not conn:
        print(f"已载真值 {len(truth)} 手;未给 --conn 且无 config.DB_DSN_SYNC,跳过 DB 对账(仅校验真值可解析)。")
        return
    if not args.after or not args.before:
        print("DB 对账需 --after/--before 框定你标注那场的时间窗(ISO,如 --after 2026-06-24T10:00:00 --before 2026-06-24T11:00:00)。")
        return
    db = fetch_db_hands(conn, args.after, args.before)
    pairs = match_hands(truth, db)
    scores = [score_hand(p["truth"], p["db"], args.abs_tol, args.rel_tol)
              for p in pairs if p["truth"] and p["db"]]
    _print_report(pairs, scores, aggregate(scores))


if __name__ == "__main__":
    main()
