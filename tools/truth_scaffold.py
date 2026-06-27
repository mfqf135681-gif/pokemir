"""tools/truth_scaffold.py — 反向校验脚手架:DB 候选真值 → 人工核错。

阶段-1 真值的【反向】产法:逐手从 DB(管线输出)生成候选真值 JSON,人对视频逐手核——
错的改、对的留。比从零标快得多(辛苦活减负)。

⚠️ 圆形验证警告(务必读):候选来自 DB,人若"看着对就过"=【确认偏差】,会把 DB 盲区里
的错一起放过 → truth 偷偷继承 DB 的错 = 部分循环论证。**安全用法**:
  - 已验过准的(端点 OCR,n=4 实测 median=0)→ 快过即可;
  - 没验过 / 高风险的(赢家归属、求解器新输出、带 _flags 的手)→ **务必独立对视频核,别只瞄 DB**。
脚本把"该独立核"的项自动标进 `_flags`,集中你的注意力。

用法(Win):
  .\\.venv\\Scripts\\python.exe tools\\truth_scaffold.py --after 2026-06-24T16:24:25 --before 2026-06-24T16:26:40 --out tools\\cand.json
然后:打开 cand.json,对 replay.mp4 逐手核;错的改;`_flags`/`_db_*` 是辅助提示,核完可删。
核好的 cand.json 直接喂 truth_score.py 对账(它忽略 _ 开头的辅助键)。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VOLUNTARY = {"fold", "check", "call", "bet", "raise", "all_in"}


def _fetch(conn_str: str, after: str, before: str):
    import psycopg2
    import psycopg2.extras
    dsn = conn_str
    for t in ("+psycopg2", "+asyncpg", "+psycopg"):
        dsn = dsn.replace(t, "")
    out = []
    with psycopg2.connect(dsn) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, started_at, raw_data, result, community_cards FROM hands "
                "WHERE started_at >= %s AND started_at <= %s ORDER BY started_at",
                (after, before))
            hands = cur.fetchall()
            for h in hands:
                cur.execute(
                    "SELECT raw_data, action_type, amount, street FROM action_events "
                    "WHERE hand_id=%s ORDER BY sequence_number", (h["id"],))
                out.append((dict(h), [dict(a) for a in cur.fetchall()]))
    return out


def scaffold_hand(idx: int, hand_row: dict, action_rows: list) -> dict:
    """DB 一手 → 候选真值 dict(带 _flags 引导独立核)。纯逻辑,可单测。"""
    rd = hand_row.get("raw_data") or {}
    init = {int(k): v for k, v in (rd.get("player_stacks_initial") or {}).items()}
    fin = {int(k): v for k, v in (rd.get("player_stacks_final") or {}).items()}
    names = {int(k): v for k, v in (rd.get("seat_names") or {}).items()}
    seats = {}
    for s in sorted(set(init) | set(fin) | set(names)):
        seats[str(s)] = {"name": names.get(s), "initial": init.get(s), "final": fin.get(s)}
    actions = []
    for a in action_rows:
        if a.get("action_type") not in VOLUNTARY:
            continue
        si = (a.get("raw_data") or {}).get("seat_index")
        if si is None:
            continue
        actions.append({"seat": int(si), "street": a.get("street"),
                        "action": a.get("action_type"), "amount": a.get("amount")})
    result = hand_row.get("result") or {}
    cr = rd.get("chip_reconstruction") or {}
    bl = rd.get("blind_level") or {}

    flags = []
    if result.get("sources_agree") is False:
        flags.append(f"⚠赢家两源分歧:+xx={result.get('win_seats_xx')} 端点={result.get('winners_endpoint')} → 独立核谁真赢")
    for f in (cr.get("flags") or []):
        flags.append(f"⚠重建flag: {f}(可能 rebuy/离场/守恒违例,核净额语义)")
    for s, v in seats.items():
        if v["initial"] == 0 or v["final"] == 0:
            flags.append(f"⚠seat{s} 端点含0(可能 join/leave/审核中)→ 核它是否真参与本手")
    if len(actions) < 2:
        flags.append("⚠自愿动作<2笔 → 可能手末漏抓,核动作完整性")

    # winners 用示例格式 [{seat,amount}];预填 +xx(比端点可靠),金额取 win_amounts_xx
    win_seats = result.get("win_seats_xx") or result.get("winners_endpoint") or []
    win_amts = result.get("win_amounts_xx") or {}
    winners = [{"seat": int(s), "amount": win_amts.get(str(s))} for s in win_seats]
    community = hand_row.get("community_cards") or [None, None, None, None, None]

    return {
        "label": f"h{idx}",
        "button_seat": rd.get("button_seat_index"),
        "blinds": {"sb": bl.get("sb"), "bb": bl.get("bb"), "ante": bl.get("ante")},
        "seats": seats,
        "actions": actions,
        "winners": winners,
        "community": community,
        "_flags": flags,   # 辅助:可疑项引导独立核;truth_score 忽略 _ 开头键,核完可删
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="反向校验脚手架:DB 候选 → 人工核")
    ap.add_argument("--after", required=True, help="started_at 下界(北京时间,服务器上海时区)")
    ap.add_argument("--before", required=True, help="started_at 上界")
    ap.add_argument("--conn", help="PG 串(不给则读 config.DB_DSN_SYNC)")
    ap.add_argument("--out", default="truth_candidate.json")
    args = ap.parse_args(argv)

    conn = args.conn
    if not conn:
        from config import DB_DSN_SYNC
        conn = DB_DSN_SYNC

    rows = _fetch(conn, args.after, args.before)
    hands = [scaffold_hand(i + 1, h, a) for i, (h, a) in enumerate(rows)]
    doc = {
        "_说明": ("⚠️反向校验候选(来自 DB 管线输出)。对视频逐手核:错的改、对的留。"
                "**_flags 标的项 + winners 务必独立核,别只瞄 DB**(防确认偏差/循环验证)。"
                "已验准的端点 OCR 可快过。核完 _flags/_db_* 可删。"),
        "session": f"scaffold_{args.after}",
        "hands": hands,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    nflag = sum(1 for h in hands if h["_flags"])
    print(f"生成 {len(hands)} 手候选 → {args.out};其中 {nflag} 手带 _flags(重点独立核这些)")


if __name__ == "__main__":
    main()
