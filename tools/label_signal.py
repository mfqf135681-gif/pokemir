"""信源验证标注【盲标侧】(2026-06-10,见 requirement-discussions/主题-信源验证.md)。

读 LabelCapturer 存的 session,对每条样本:**先只显 crop(+宽图),用户盲敲真值,敲完才揭示
机器读值并比对** → 算 precision + 存带标注数据集 labeled.jsonl。

五硬约束落点:
  ② 盲标 —— 揭示机器读值【在用户输入之后】,杜绝"先摆读数让你点对"的锚定偏差。
  ④ 宽图防框歪 —— 同时开 crop 和 wide(整窗画红框);--no-show 时打印两图路径自己开。
  ⑤ 只验逐帧 —— 比的是"这帧 crop 机器读得准不准",**不验这手最终用的值对不对**(后者归 #226 守恒层)。

用法:
  python tools/label_signal.py data/label_sessions/pot_size_<ts>          # 弹图盲标
  python tools/label_signal.py <session> --no-show                         # 无显示环境:打印路径
  python tools/label_signal.py <session> --resume                          # 续标(跳过已标)
  python tools/label_signal.py --selftest                                  # 纯逻辑自检(Linux)
输入约定:数字=真值 / 回车=跳过(不计) / x=该帧框歪或不可读(记录、不计 precision) / q=存盘退出。
"""
import argparse
import json
import os
import sys


def _to_num(s):
    """'532' / '532.0' / '1,234' → float;空/非数 → None。"""
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


TEXT_SIGNALS = {"action", "id", "name"}   # 文本信号(动作词/玩家名):按字符串严格比,非数字


def _is_text(rec):
    return rec.get("signal") in TEXT_SIGNALS


def is_match(truth, read_value, tol=0.0):
    """数字信号:两边都是数且 |差|≤tol。tol=0 即严格相等(底池整数默认严格)。"""
    t, r = _to_num(truth), _to_num(read_value)
    if t is None or r is None:
        return False
    return abs(t - r) <= tol


def is_match_text(truth, read_value):
    """文本信号(action/id):去空白后严格相等;空读=不匹配。"""
    r = "" if read_value is None else str(read_value).strip()
    return r != "" and str(truth).strip() == r


def score(labeled, tol=0.0):
    """纯逻辑(Linux 可单测)。数字信号走 is_match;文本信号(action/id/name)走 is_match_text。
    truth 约定:已标真值 / None 或 ''=跳过(不计) / 'x'=框歪不可读(记录、剔出 precision 分母)。
    返回 {n, judged, match, mismatch, read_miss, unreadable, precision, mismatches[]}。"""
    judged = match = read_miss = unreadable = 0
    mismatches = []
    for rec in labeled:
        truth = rec.get("truth")
        if truth is None or str(truth).strip() == "":
            continue  # 跳过未标
        if str(truth).strip().lower() == "x":
            unreadable += 1
            continue  # 框歪/不可读 → 不进 precision 分母
        judged += 1
        rv = rec.get("read_value")
        text = _is_text(rec)
        read_empty = (rv is None or str(rv).strip() == "") if text else (_to_num(rv) is None)
        matched = is_match_text(truth, rv) if text else is_match(truth, rv, tol)
        if read_empty:
            read_miss += 1  # 有真值但机器读空 → 未命中
            mismatches.append({"id": rec.get("id"), "truth": truth, "read": rv, "kind": "read_none"})
        elif matched:
            match += 1
        else:
            mismatches.append({"id": rec.get("id"), "truth": truth, "read": rv, "kind": "wrong"})
    mismatch = judged - match
    precision = (match / judged) if judged else None
    return {"n": len(labeled), "judged": judged, "match": match, "mismatch": mismatch,
            "read_miss": read_miss, "unreadable": unreadable, "precision": precision,
            "mismatches": mismatches}


def _selftest():
    rows = [
        {"id": 0, "read_value": 532, "truth": "532"},     # match
        {"id": 1, "read_value": 1984, "truth": "577"},    # wrong
        {"id": 2, "read_value": None, "truth": "40"},     # read_none
        {"id": 3, "read_value": 50, "truth": ""},         # skip
        {"id": 4, "read_value": 999, "truth": "x"},       # unreadable(框歪)
        {"id": 5, "read_value": "跟注", "truth": "跟注", "signal": "action"},  # 文本 match
        {"id": 6, "read_value": "加注", "truth": "跟注", "signal": "action"},  # 文本 wrong
        {"id": 7, "read_value": "", "truth": "向光", "signal": "id"},          # 文本 read_none
    ]
    s = score(rows)
    assert s["judged"] == 6 and s["match"] == 2 and s["mismatch"] == 4, s
    assert s["read_miss"] == 2 and s["unreadable"] == 1, s
    assert abs(s["precision"] - 1 / 3) < 1e-9, s
    assert is_match("532", 532.0) and not is_match("577", 1984)
    assert is_match_text("跟注", "跟注") and not is_match_text("跟注", "加注") and not is_match_text("向光", "")
    assert _to_num("1,234") == 1234.0 and _to_num("") is None and _to_num("abc") is None
    print("selftest OK", s)


def _load(session_dir):
    path = os.path.join(session_dir, "samples.jsonl")
    if not os.path.exists(path):
        sys.exit(f"找不到 {path}(先用 POKEMIR_LABEL_SIGNAL=pot_size 跑主程序采集)")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _save(session_dir, labeled):
    out = os.path.join(session_dir, "labeled.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for r in labeled:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", help="LabelCapturer 存的 session 目录")
    ap.add_argument("--no-show", action="store_true", help="不弹窗,打印图路径自己开")
    ap.add_argument("--resume", action="store_true", help="续标:跳过 labeled.jsonl 已标的 id")
    ap.add_argument("--tol", type=float, default=0.0, help="匹配容差(底池默认严格 0)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return
    if not args.session:
        ap.error("需要 session 目录(或 --selftest)")

    samples = _load(args.session)
    done = {}
    if args.resume and os.path.exists(os.path.join(args.session, "labeled.jsonl")):
        for r in _load_labeled(args.session):
            done[r["id"]] = r

    cv2 = None
    if not args.no_show:
        try:
            import cv2 as _cv2
            cv2 = _cv2
        except ImportError:
            print("[warn] 无 cv2,转 --no-show(打印路径)")

    labeled = []
    print(f"共 {len(samples)} 帧。数字=真值 / 回车=跳过 / x=框歪不可读 / q=存盘退出\n")
    for rec in samples:
        if rec["id"] in done:
            labeled.append(done[rec["id"]])
            continue
        crop_p = os.path.join(args.session, rec["crop"])
        wide_p = os.path.join(args.session, rec["wide"]) if rec.get("wide") else None
        if cv2 is not None:
            img = cv2.imread(crop_p)
            if img is not None:
                cv2.imshow("crop (识别器实际吃的)", img)
            if wide_p:
                w = cv2.imread(wide_p)
                if w is not None:
                    cv2.imshow("wide (红框=切的位置,看框歪没)", w)
            cv2.waitKey(1)
        else:
            print(f"  crop: {crop_p}" + (f"  wide: {wide_p}" if wide_p else ""))
        # 盲标:此刻【绝不显示 rec['read_value']】
        ans = input(f"[#{rec['id']} reason={rec.get('reason')}] 真值? ").strip()
        if ans.lower() == "q":
            break
        rv = rec.get("read_value")
        rec2 = dict(rec)
        rec2["truth"] = ans
        labeled.append(rec2)
        # 敲完才揭示 + 即时反馈
        if ans and ans.lower() != "x":
            verdict = "✅" if is_match(ans, rv, args.tol) else ("⛔读空" if _to_num(rv) is None else "❌")
            print(f"    机器读={rv}  {verdict}")
    if cv2 is not None:
        cv2.destroyAllWindows()

    out = _save(args.session, labeled)
    s = score(labeled, args.tol)
    print(f"\n=== {os.path.basename(args.session)} ===")
    print(f"已标 judged={s['judged']}  命中={s['match']}  错={s['mismatch']}"
          f"(其中读空 {s['read_miss']})  框歪/不可读={s['unreadable']}")
    if s["precision"] is not None:
        print(f"逐帧 precision = {s['precision']:.1%}  (只验逐帧读,不验这手用值——后者归守恒层)")
    if s["mismatches"]:
        print("错例(供查 ROI/识别):")
        for m in s["mismatches"][:20]:
            print(f"  #{m['id']} 真={m['truth']} 读={m['read']} ({m['kind']})")
    print(f"→ 存 {out}")


def _load_labeled(session_dir):
    with open(os.path.join(session_dir, "labeled.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


if __name__ == "__main__":
    main()
