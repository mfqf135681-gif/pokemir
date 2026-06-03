"""tools/replay_reconstruct.py — 砖1b:录制帧 → stack 轨迹 → reconstruct → 对真值量捕获率

离线回放(无 fps 压力,用现有 OCR/CNN 读)验证 §15 架构**正确性**(与速度解耦)。
设计:可测核(manifest 解析 / 窗口化 / reconstruct 集成 / 真值比对)+ **隔离的盲适配器**
(read_stack / count_community,Win-only OCR/CNN)。--mock 离线自测核。

流程:
  录制 session(帧+manifest)→ 指定一手的时间窗(--start/--end,从你标注的"第几分钟")
  → 逐帧:每座读 stack(OCR)+ 数公共牌(CNN)→ stack_series / community_series
  → pipeline.reconstruct → 重建动作 → 对真值(compare_truth 格式)量捕获率

⚠️ Win-only(真跑需 OCR/CNN + 录制帧);Linux 仅 --mock 自测核 + 语法。
设计 doc: §15 / §15.2;砖1a=pipeline/reconstruct.py(已自测)
用法(Win):
  .\\.venv\\Scripts\\python.exe tools\\replay_reconstruct.py --session data\\recordings\\<ts> \\
      --profile party_poker_8 --start 120 --end 195 --truth hand1_truth.txt \\
      --sb 2 --bb 4 --ante 4 --pot 470
"""

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "pipeline"))
# standalone 导入 reconstruct.py,绕开 pipeline/__init__(它拉 detector→imagehash)
import reconstruct as _recon  # noqa: E402
reconstruct = _recon.reconstruct


# ── 可测核:profile ROI 提取 ──────────────────────────────────────────
def load_rois(profile_path):
    """→ (stack_rois {seat:[l,t,w,h]}, community_rois [[l,t,w,h]×5])"""
    p = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    stack_rois = {}
    for s in p.get("seats", []):
        if "stack" in s and s["stack"]:
            stack_rois[int(s["seat_index"])] = s["stack"]
    community_rois = p.get("community_cards", []) or []
    return stack_rois, community_rois


def load_manifest(session):
    """manifest.jsonl → (meta, frames [(i, file, t_mono)])"""
    lines = (Path(session) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    frames = []
    for ln in lines[1:]:
        if not ln.strip():
            continue
        d = json.loads(ln)
        frames.append((d["i"], d["file"], d.get("t_mono", 0.0)))
    return meta, frames


# ── 盲适配器(Win-only,OCR/CNN;--mock 跳过)───────────────────────────
def read_stack(img, roi, ocr):
    """裁 stack ROI → OCR 数字 → float|None。⚠️ Win-only,未在 Linux 验。"""
    l, t, w, h = roi
    crop = img[t:t + h, l:l + w]
    txt = ocr.read_text(crop, allowlist="0123456789")
    digits = "".join(c for c in txt if c.isdigit())
    return float(digits) if digits else None


def read_stack_ex(img, roi, ocr):
    """BUG2/R15:读 stack,若命中 '%'(all-in 显胜率,筹码被盖)→ (None, True);否则 (float|None, False)。
    allowlist 含 % 以侦测;⚠️ 单用途 stack ROI 才可这样收窄(见 ocr-allowlist 红线)。Win-only。"""
    l, t, w, h = roi
    txt = ocr.read_text(img[t:t + h, l:l + w], allowlist="0123456789%") or ""
    if "%" in txt:
        return None, True
    digits = "".join(c for c in txt if c.isdigit())
    return (float(digits) if digits else None), False


def _white_frac(crop, white_th):
    """三通道都 > white_th 的像素占比(牌面=大片白;空 felt≈0)。"""
    if crop is None or crop.size == 0:
        return 0.0
    return float((crop.min(axis=2) > white_th).mean())


def count_community(img, community_rois, white_th=170, frac_th=0.12):
    """数公共牌:卡片是大面积白色,空板位是暗 felt → 按白占比判(不用 CNN,稳+快)。"""
    n = 0
    for (l, t, w, h) in community_rois:
        if _white_frac(img[t:t + h, l:l + w], white_th) > frac_th:
            n += 1
    return n


def build_series_real(session, frames, stack_rois, community_rois, start, end, decimate,
                      white_th=170, frac_th=0.12):
    """Win 路径:逐帧读 → stack_series / community_series。公共牌用白占比(不用 CNN)。"""
    import cv2
    from recognition.ocr import OCREngine
    ocr = OCREngine(gpu=True, name="replay")
    stack_series = {s: [] for s in stack_rois}
    allin_marks = {s: [] for s in stack_rois}  # BUG2:stack 区显胜率% 的时刻 → all-in
    community_series = []
    fdir = Path(session) / "frames"
    for k, (i, fn, t) in enumerate(frames):
        if t < start or t > end or (k % decimate):
            continue
        img = cv2.imread(str(fdir / fn))
        if img is None:
            continue
        for s, roi in stack_rois.items():
            v, is_ai = read_stack_ex(img, roi, ocr)
            stack_series[s].append((t, v))
            if is_ai:
                allin_marks[s].append(t)
        community_series.append((t, count_community(img, community_rois, white_th, frac_th)))
    return stack_series, community_series, allin_marks


# ── §17 持久信号试验(T129):读 action 词 + amount 下注区,验街内持久 + 可读性 ──
# 动作词候选字符(弃牌/跟注/让牌/加注/下注/全下/过牌/盖牌);OCR allowlist 收窄抗噪
_ACTION_ALLOW = "弃牌跟注让加下全过盖"


def load_action_rois(profile_path):
    """→ (action_rois {seat:[l,t,w,h]} 动作词, amount_rois {seat:[..]} 下注区金额)"""
    p = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    action_rois, amount_rois = {}, {}
    for s in p.get("seats", []):
        si = int(s["seat_index"])
        if s.get("action"):
            action_rois[si] = s["action"]
        if s.get("amount"):
            amount_rois[si] = s["amount"]
    return action_rois, amount_rois


def load_pot_roi(profile_path):
    """→ (pot_roi [l,t,w,h]|None, pot_prev_roi |None)。底池=守恒锚,T125 验它读不读得到 99%。"""
    p = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    return p.get("pot_size"), p.get("pot_size_previous")


def read_word(img, roi, ocr, allowlist=_ACTION_ALLOW):
    """裁动作词 ROI → OCR(收窄 allowlist)→ str|None。⚠️ Win-only。"""
    l, t, w, h = roi
    txt = ocr.read_text(img[t:t + h, l:l + w], allowlist=allowlist)
    txt = "".join(c for c in (txt or "") if c.strip())
    return txt or None


def collapse_changes(series):
    """[(t, val)] → 只在值变化处留点(None 也算一种值)。纯函数,验"街内持久"。"""
    out, prev = [], object()  # sentinel ≠ 任何值
    for t, v in series:
        if v != prev:
            out.append((t, v))
            prev = v
    return out


def build_action_series(session, frames, action_rois, amount_rois, community_rois,
                        start, end, decimate, white_th=170, frac_th=0.12):
    """Win 路径:逐帧读 action 词 + amount 下注区 + 公共牌 → 三个时间序列。"""
    import cv2
    from recognition.ocr import OCREngine
    ocr = OCREngine(gpu=True, name="replay")
    act = {s: [] for s in action_rois}
    amt = {s: [] for s in amount_rois}
    community_series = []
    fdir = Path(session) / "frames"
    for k, (i, fn, t) in enumerate(frames):
        if t < start or t > end or (k % decimate):
            continue
        img = cv2.imread(str(fdir / fn))
        if img is None:
            continue
        for s, roi in action_rois.items():
            act[s].append((t, read_word(img, roi, ocr)))
        for s, roi in amount_rois.items():
            amt[s].append((t, read_stack(img, roi, ocr)))  # amount=数字,复用 read_stack
        community_series.append((t, count_community(img, community_rois, white_th, frac_th)))
    return act, amt, community_series


def build_fuse_series(session, frames, stack_rois, amount_rois, community_rois,
                      start, end, decimate, white_th=170, frac_th=0.12):
    """一遍读 stack + amount下注区 + 公共牌 → 三序列(--fuse:量 stack∪下注区 覆盖)。"""
    import cv2
    from recognition.ocr import OCREngine
    ocr = OCREngine(gpu=True, name="replay")
    stack_series = {s: [] for s in stack_rois}
    bet_series = {s: [] for s in amount_rois}
    community_series = []
    fdir = Path(session) / "frames"
    for k, (i, fn, t) in enumerate(frames):
        if t < start or t > end or (k % decimate):
            continue
        img = cv2.imread(str(fdir / fn))
        if img is None:
            continue
        for s, roi in stack_rois.items():
            stack_series[s].append((t, read_stack(img, roi, ocr)))
        for s, roi in amount_rois.items():
            bet_series[s].append((t, read_stack(img, roi, ocr)))
        community_series.append((t, count_community(img, community_rois, white_th, frac_th)))
    return stack_series, bet_series, community_series


def build_truth_series(session, frames, stack_rois, amount_rois, community_rois,
                       start, end, decimate, white_th=170, frac_th=0.12):
    """§19 --truth 融合:一遍读 stack(+all-in 标记) + 下注区 amount + 公共牌。"""
    import cv2
    from recognition.ocr import OCREngine
    ocr = OCREngine(gpu=True, name="replay")
    stack_series = {s: [] for s in stack_rois}
    bet_series = {s: [] for s in amount_rois}
    allin_marks = {s: [] for s in stack_rois}
    community_series = []
    fdir = Path(session) / "frames"
    for k, (i, fn, t) in enumerate(frames):
        if t < start or t > end or (k % decimate):
            continue
        img = cv2.imread(str(fdir / fn))
        if img is None:
            continue
        for s, roi in stack_rois.items():
            v, is_ai = read_stack_ex(img, roi, ocr)
            stack_series[s].append((t, v))
            if is_ai:
                allin_marks[s].append(t)
        for s, roi in amount_rois.items():
            bet_series[s].append((t, read_stack(img, roi, ocr)))
        community_series.append((t, count_community(img, community_rois, white_th, frac_th)))
    return stack_series, bet_series, community_series, allin_marks


# ── 真值比对(可测)────────────────────────────────────────────────────
def compare_to_truth(actions, truth_path, tol=2.0):
    """reconstruct 的 ChipAction vs 真值文件(compare_truth 格式)→ 捕获率。
    按 (street, amount≈) 贪心匹配筹码动作。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from compare_truth import parse_labels, CHIP_ACTIONS
    hands = parse_labels(Path(truth_path).read_text(encoding="utf-8"))
    true_chips = []
    for h in hands:
        for a in h.actions:
            if a.type in CHIP_ACTIONS and a.amount is not None:
                true_chips.append((a.street, a.type, a.amount))
    recon = [(a.street, a.atype, a.chips_in) for a in actions]
    used = [False] * len(recon)
    matched, missed = 0, []
    for (st, ty, amt) in true_chips:
        hit = -1
        for j, (rst, rty, ramt) in enumerate(recon):
            if used[j] or rst != st:
                continue
            if abs(ramt - amt) <= tol:
                hit = j
                break
        if hit >= 0:
            used[hit] = True
            matched += 1
        else:
            missed.append((st, ty, amt))
    extra = [recon[j] for j in range(len(recon)) if not used[j]]
    rate = matched / len(true_chips) if true_chips else float("nan")
    return {"true": len(true_chips), "matched": matched, "missed": missed,
            "extra": extra, "capture_rate": rate}


def compare_to_truth_fused(stack_actions, bet_candidates, truth_path, tol=2.0):
    """§19 融合捕获率:truth 金额匹配 stack增量 OR 下注区call-to(任一对上即命中)。
    治两病:① call-to vs 增量口径假象(下注区=call-to=用户口径)② all-in 漏(注区显 all-in 钱)。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from compare_truth import parse_labels, CHIP_ACTIONS
    hands = parse_labels(Path(truth_path).read_text(encoding="utf-8"))
    true_chips = [(a.street, a.amount) for h in hands for a in h.actions
                  if a.type in CHIP_ACTIONS and a.amount is not None]
    cands = [(a.street, a.chips_in) for a in stack_actions] + list(bet_candidates)
    used = [False] * len(cands)
    matched, missed = 0, []
    for (st, amt) in true_chips:
        hit = -1
        for j, (cst, cv) in enumerate(cands):
            if not used[j] and cst == st and abs(cv - amt) <= tol:
                hit = j
                break
        if hit >= 0:
            used[hit] = True
            matched += 1
        else:
            missed.append((st, amt))
    rate = matched / len(true_chips) if true_chips else float("nan")
    return {"true": len(true_chips), "matched": matched, "missed": missed, "capture_rate": rate}


def main():
    ap = argparse.ArgumentParser(description="砖1b 回放重建 + 捕获率(T127)")
    ap.add_argument("--session"); ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--start", type=float, default=0); ap.add_argument("--end", type=float, default=1e9)
    ap.add_argument("--decimate", type=int, default=1, help="每 N 帧处理一帧(提速,默认全用)")
    ap.add_argument("--truth", help="真值文件(compare_truth 格式)")
    ap.add_argument("--sb", type=float, default=2); ap.add_argument("--bb", type=float, default=4)
    ap.add_argument("--ante", type=float, default=4); ap.add_argument("--pot", type=float, default=0)
    ap.add_argument("--white-th", type=int, default=170, help="公共牌白像素阈值(三通道都>此=白)")
    ap.add_argument("--frac-th", type=float, default=0.12, help="白占比>此 = 该板位有牌")
    ap.add_argument("--mock", action="store_true", help="离线自测核(不读帧)")
    ap.add_argument("--dump-stacks", action="store_true",
                    help="只读+打印每座 stack 轨迹(验读取质量,不需真值/不跑 reconstruct)")
    ap.add_argument("--dump-actions", action="store_true",
                    help="§17/T129:只读+打印每座 action 词 + amount 下注区时间序列(验街内持久+可读性)")
    ap.add_argument("--fuse", action="store_true",
                    help="§17/T129:量 stack∪下注区 融合覆盖(逐手 stack重建 vs 下注区反推 对比;无真值时看相对覆盖+一致性)")
    ap.add_argument("--dump-pot", action="store_true",
                    help="T125:只读+打印底池(pot_size)时间序列(验守恒锚读取可靠性,目标近 99 percent)")
    args = ap.parse_args()

    if args.mock:
        _self_test()
        return
    if not args.session:
        ap.error("给 --session 或 --mock")

    profile_path = Path("rois") / f"{args.profile}.json"
    meta, frames = load_manifest(args.session)

    if args.dump_actions:
        action_rois, amount_rois = load_action_rois(profile_path)
        _, community_rois = load_rois(profile_path)
        print(f"session {args.session}: {len(frames)} 帧, 窗[{args.start},{args.end}], "
              f"{len(action_rois)} 座 action ROI / {len(amount_rois)} 座 amount ROI")
        act, amt, community_series = build_action_series(
            args.session, frames, action_rois, amount_rois, community_rois,
            args.start, args.end, args.decimate, args.white_th, args.frac_th)
        print("\n=== 公共牌张数 over time(街上下文)===")
        prev = None
        for (t, n) in community_series:
            if n != prev:
                print(f"  t{t:.1f}: {n} 张")
                prev = n
        print("\n=== 每座 action 词 + amount 下注区(按值变化折叠;验街内持久)===")
        for s in sorted(set(act) | set(amt)):
            aw = collapse_changes(act.get(s, []))
            am = collapse_changes(amt.get(s, []))
            na = sum(1 for _, v in act.get(s, []) if v is not None)
            nm = sum(1 for _, v in amt.get(s, []) if v is not None)
            print(f"\nseat{s}: action {len(act.get(s,[]))}读/{na}非空 | amount {len(amt.get(s,[]))}读/{nm}非空")
            print(f"  action 变化: {[(round(t,1), v) for t, v in aw]}")
            print(f"  amount 变化: {[(round(t,1), (int(v) if v is not None else None)) for t, v in am]}")
        print("\n判读:① 动作做出后 action 词/amount 应在本街内【保持不变】(持久),街末清/换街变;"
              "② 全座都应读得出(非空多);③ amount 跳变序列应能复原各街投入。")
        return

    if args.fuse:
        stack_rois, community_rois = load_rois(profile_path)
        _, amount_rois = load_action_rois(profile_path)
        print(f"session {args.session}: {len(frames)} 帧, 窗[{args.start},{args.end}], "
              f"stack {len(stack_rois)} 座 / amount {len(amount_rois)} 座")
        stack_series, bet_series, community_series = build_fuse_series(
            args.session, frames, stack_rois, amount_rois, community_rois,
            args.start, args.end, args.decimate, args.white_th, args.frac_th)
        config = {"sb": args.sb, "bb": args.bb, "ante": args.ante, "pot": args.pot,
                  "seats": list(stack_rois.keys())}
        windows = _recon.segment_hands(stack_series, community_series, config)
        agg = {k: 0 for k in ("both_agree", "both_disagree", "stack_only", "bet_only", "n_stack", "n_bet", "union")}
        print(f"\n=== 融合覆盖对比({len(windows)} 手:stack 重建 vs 下注区反推)===")
        for hi, (t0, t1) in enumerate(windows, 1):
            ss, cs = _recon.slice_series(stack_series, community_series, t0, t1)
            bs = {s: [(t, v) for (t, v) in bet_series.get(s, []) if t0 <= t < t1] for s in bet_series}
            res = reconstruct(ss, cs, config)
            bacts = _recon.actions_from_bets(bs, cs, config)
            cov = _recon.compare_coverage(res.actions, bacts)
            for k in agg:
                agg[k] += cov[k]
            print(f"  手{hi} t[{t0:.0f},{t1:.0f}]: stack {cov['n_stack']} / 下注区 {cov['n_bet']} "
                  f"| 都中{cov['both_agree']} 不合{cov['both_disagree']} 仅stack{cov['stack_only']} 仅下注区{cov['bet_only']}")
        print(f"\n=== 全手聚合 ===")
        print(f"  stack 单独抓到: {agg['n_stack']}")
        print(f"  下注区单独抓到: {agg['n_bet']}")
        print(f"  两者都中且金额合: {agg['both_agree']}  | 都中但金额不合: {agg['both_disagree']}")
        print(f"  仅 stack(下注区漏): {agg['stack_only']}  | 仅下注区(stack 漏): {agg['bet_only']}")
        print(f"  **融合并集(天花板代理): {agg['union']}**  ← 比单 stack({agg['n_stack']})多 {agg['union']-agg['n_stack']}")
        print("\n判读:bet_only 高 = 下注区补回 stack 漏的(融合值);both_agree 高 = 两信号互证可靠;"
              "both_disagree 高 = 某信号读错(需查)。⚠️ 无真值=只能看相对覆盖,非绝对捕获率。")
        return

    if args.dump_pot:
        import cv2
        from recognition.ocr import OCREngine
        pot_roi, pot_prev_roi = load_pot_roi(profile_path)
        _, community_rois = load_rois(profile_path)
        if not pot_roi:
            ap.error("profile 无 pot_size ROI")
        ocr = OCREngine(gpu=True, name="replay")
        fdir = Path(args.session) / "frames"
        pot_series, community_series = [], []
        for k, (i, fn, t) in enumerate(frames):
            if t < args.start or t > args.end or (k % args.decimate):
                continue
            img = cv2.imread(str(fdir / fn))
            if img is None:
                continue
            pot_series.append((t, read_stack(img, pot_roi, ocr)))
            community_series.append((t, count_community(img, community_rois, args.white_th, args.frac_th)))
        nn = sum(1 for _, v in pot_series if v is not None)
        print(f"session {args.session}: pot ROI {pot_roi} | {len(pot_series)}读/{nn}非空")
        print("\n=== 公共牌张数 over time(手/街上下文)===")
        prev = None
        for (t, n) in community_series:
            if n != prev:
                print(f"  t{t:.1f}: {n} 张")
                prev = n
        print("\n=== 底池 pot_size over time(按值变化折叠)===")
        for (t, v) in collapse_changes(pot_series):
            print(f"  t{t:.1f}: {int(v) if v is not None else None}")
        print("\n判读(守恒锚可靠性):① 手内底池应【单调升】(每动作加注),手末派彩后 reset(对齐公共牌归零);"
              "② 非空率应高、无乱跳/垃圾值;③ 若手内出现下降(非手末)或读空 → 锚点不稳,99% 有风险。")
        return

    stack_rois, community_rois = load_rois(profile_path)
    print(f"session {args.session}: {len(frames)} 帧, 时间窗 [{args.start},{args.end}], "
          f"{len(stack_rois)} 座 stack ROI")
    bet_series = {}
    if args.truth:  # §19 融合比对:同时读下注区(call-to 候选 + 补 all-in)
        _, amount_rois = load_action_rois(profile_path)
        stack_series, bet_series, community_series, allin_marks = build_truth_series(
            args.session, frames, stack_rois, amount_rois, community_rois,
            args.start, args.end, args.decimate, args.white_th, args.frac_th)
    else:
        stack_series, community_series, allin_marks = build_series_real(
            args.session, frames, stack_rois, community_rois, args.start, args.end, args.decimate,
            args.white_th, args.frac_th)

    if args.dump_stacks:
        # 校准辅助:抽几帧打印每个板位的白占比(看空板 vs 有牌差多少,定 --frac-th)
        import cv2
        fdir = Path(args.session) / "frames"
        print("\n=== 公共牌板位 白占比 抽样(校准 --frac-th)===")
        sampled = 0
        for (i, fn, t) in frames:
            if t < args.start or t > args.end or (i % max(args.decimate * 20, 20)):
                continue
            img = cv2.imread(str(fdir / fn))
            if img is None:
                continue
            fr = [round(_white_frac(img[tt:tt + h, l:l + w], args.white_th), 2)
                  for (l, tt, w, h) in community_rois]
            print(f"  t{t:.1f}: 板位白占比 {fr} → 判 {count_community(img, community_rois, args.white_th, args.frac_th)} 张")
            sampled += 1
            if sampled >= 6:
                break
        print("\n=== 每座 stack 轨迹(验读取质量)===")
        for s in sorted(stack_series):
            obs = stack_series[s]
            nn = sum(1 for _, v in obs if v is not None)
            plats = _recon.fuse_plateaus(obs)
            print(f"seat{s}: {len(obs)}读/{nn}非空 | 平台 {[(round(t, 1), int(v)) for t, v in plats]}")
        print("\n=== 公共牌张数 over time(验分街信号)===")
        prev = None
        for (t, n) in community_series:
            if n != prev:
                print(f"  t{t:.1f}: {n} 张")
                prev = n
        print("  (应:每手 0→3→4→5 然后 reset 回 0;若一直 5/3 或乱跳 → 公共牌读取/iscard 误判)")
        print("\n判读:stack 平台应单调下降(台阶=投入);公共牌应随街 0→3→4→5。")
        return

    config = {"sb": args.sb, "bb": args.bb, "ante": args.ante, "pot": args.pot,
              "seats": list(stack_rois.keys())}
    windows = _recon.segment_hands(stack_series, community_series, config)
    print(f"\n=== 手分段:检测到 {len(windows)} 手 ===")
    all_actions, all_bet_cands = [], []
    for hi, (t0, t1) in enumerate(windows, 1):
        ss, cs = _recon.slice_series(stack_series, community_series, t0, t1)
        am = {s: [t for t in allin_marks.get(s, []) if t0 <= t < t1] for s in allin_marks}
        res = reconstruct(ss, cs, config, allin_marks=am)
        all_actions.extend(res.actions)
        if bet_series:  # §19 下注区 call-to 候选(融合比对用)
            bs = {s: [(t, v) for (t, v) in bet_series.get(s, []) if t0 <= t < t1] for s in bet_series}
            all_bet_cands.extend(_recon.bet_callto_candidates(bs, cs, config))
        print(f"\n--- 手 {hi}  t[{t0:.1f},{t1:.1f}] ---")
        for a in sorted(res.actions, key=lambda x: x.t):
            print(f"  seat{a.seat} {a.street} {a.atype} 投入{a.chips_in:.0f} @t{a.t:.1f}")
        for n in res.notes:
            if any(k in n for k in ("涨", "ante", "胜率", "all-in")):  # 派彩/ante排除/all-in反解
                print("  " + n)
    if args.truth:
        cmp = compare_to_truth(all_actions, args.truth)
        fus = compare_to_truth_fused(all_actions, all_bet_cands, args.truth)
        print(f"\n=== 捕获率(vs 真值,全手聚合)===")
        print(f"  真值筹码动作 {cmp['true']}")
        print(f"  ① stack-only:  命中 {cmp['matched']} | 捕获率 {cmp['capture_rate']*100:.0f}%")
        print(f"  ② 融合(stack增量 ∪ 下注区call-to):命中 {fus['matched']} | 捕获率 {fus['capture_rate']*100:.0f}%"
              f"  ← 治 call-to口径 + 补 all-in")
        if fus["missed"]:
            print("  融合后仍漏(真信号缺口):", fus["missed"])
        if cmp["extra"]:
            print("  stack 多/假:", cmp["extra"])


def _self_test():
    """离线验核:mock stack_series(Ts2h5h 式)→ reconstruct → 对 mock 真值量捕获率。"""
    import tempfile
    stack_series = {
        0: [(0, 213), (1, 213), (2, 999), (3, 213), (5, 0), (6, 0), (7, 0)],  # all-in 213
        1: [(0, 500), (1, 500), (5, 287), (6, 287), (7, 287)],                # call 213
    }
    community = [(0, 0), (4.5, 3)]
    config = {"sb": 2, "bb": 4, "ante": 4, "pot": 470, "seats": [0, 1, 2, 3, 4, 5, 6, 7]}
    res = reconstruct(stack_series, community, config)
    truth = "# hand mock\nflop: SB all_in 213, BB call 213\n"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(truth); tp = f.name
    cmp = compare_to_truth(res.actions, tp)
    os.unlink(tp)
    assert cmp["matched"] == 2, cmp
    assert abs(cmp["capture_rate"] - 1.0) < 1e-9, cmp
    print(f"✅ self-test 核通过:重建 {len(res.actions)} 动作,对 mock 真值捕获率 "
          f"{cmp['capture_rate']*100:.0f}%(matched {cmp['matched']}/{cmp['true']})")
    print("   (真跑接 Win 录制帧 + OCR/CNN 读 stack;此处仅验编排+比对核)")

    # §17/T129:collapse_changes 折叠逻辑(街内持久=同值连续帧塌成一点)
    seq = [(0.0, None), (1.0, None), (2.0, "跟注"), (3.0, "跟注"), (4.0, "加注"),
           (5.0, "加注"), (6.0, None)]  # 模拟:闲置→跟注(持久2帧)→加注(持久2帧)→街末清
    col = collapse_changes(seq)
    assert col == [(0.0, None), (2.0, "跟注"), (4.0, "加注"), (6.0, None)], col
    # amount 同理:本街投入 4→4→58(re-raise)→清
    amt = collapse_changes([(0, None), (1, 4), (2, 4), (3, 58), (4, 58), (5, None)])
    assert amt == [(0, None), (1, 4), (3, 58), (5, None)], amt
    print("✅ collapse_changes 核通过:同值连续帧塌成一点(动作词 + amount 跳变序列)")


if __name__ == "__main__":
    main()

# ── [WIN 验证 CHECKLIST] ──────────────────────────────────────────────
#  1. 先有砖1b 录制(record_frames mss 窗口帧)+ 该段一手的 history 真值文件
#  2. 从你标注的"第几分钟"定 --start/--end(秒,manifest t_mono)圈出那一手
#  3. 跑:--session ... --start S --end E --truth hand.txt --pot <该手底池> --ante 4
#  4. 看"重建动作" + "捕获率" → 这是 §15 架构在真实数据上的首个系统级数字
#  ⚠️ read_stack/count_community 我 Linux 没验;若 stack 读全 None / 公共牌数不对,
#     先单帧 debug 这两个适配器(ROI 对齐 / allowlist / CNN 输入)。
