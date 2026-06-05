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
import solver as _solver  # noqa: E402  砖2 守恒/合法性求解器
import active_set as _active  # noqa: E402  活跃集判定纯逻辑(在手区间)
import conservation as _cons  # noqa: E402  整手守恒判级(同口径 v_hand_conservation)


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
                      white_th=170, frac_th=0.12, button_rois=None):
    """Win 路径:逐帧读 → stack_series / community_series (+ button_series 若给 button_rois)。
    公共牌用白占比(不用 CNN)。T138:按钮也读 → 守恒路径也能用按钮权威切手
    (治公共牌假 reset 把 1 手切碎)。"""
    import cv2
    from recognition.ocr import OCREngine
    ocr = OCREngine(gpu=True, name="replay")
    stack_series = {s: [] for s in stack_rois}
    allin_marks = {s: [] for s in stack_rois}  # BUG2:stack 区显胜率% 的时刻 → all-in
    community_series = []
    button_series = []  # T138:D 按钮白占比 argmax → 切手权威(比公共牌 reset 稳)
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
        if button_rois:  # 同 build_truth_series:白占比 argmax 定 D 座(超阈才认,否则 None)
            best_s, best_wf = None, 0.0
            for s, (l, tt, w, h) in button_rois.items():
                wf = _white_frac(img[tt:tt + h, l:l + w], white_th)
                if wf > best_wf:
                    best_s, best_wf = s, wf
            button_series.append((t, best_s if best_wf > frac_th else None))
    return stack_series, community_series, allin_marks, button_series


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


def load_win_rois(profile_path):
    """→ {seat: win_amount ROI}。T130:'+xx' 结算=手边界(普适,翻牌前结束的手也有)。"""
    p = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    return {int(s["seat_index"]): s["win_amount"] for s in p.get("seats", []) if s.get("win_amount")}


def load_button_rois(profile_path):
    """→ {seat: button_indicator ROI}。切手信号:D 按钮(纯白高对比)移座 = 新手开始。"""
    p = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    return {int(s["seat_index"]): s["button_indicator"] for s in p.get("seats", []) if s.get("button_indicator")}


def _write_conservation_db(records, session, run_label=""):
    """把每手守恒结果写进 VPS postgres replay_conservation 表(Win→VPS Tailscale 通路)。

    幂等:同 (run_session, run_label) 先删后插;表不存在则建。仿 replay_corrections 模式。
    连库失败=优雅降级(只打印告警,不中断;控制台对比表仍在)。⚠️ 需 Win .env 配好
    POKEMIR_DB_DSN_SYNC(指向 100.101.105.46:5432)。
    """
    import psycopg2
    sys.path.insert(0, _ROOT)
    from config import DB_DSN_SYNC
    sess = str(session).replace("\\", "/").rstrip("/").split("/")[-1]  # 取录像目录名当 key
    try:
        con = psycopg2.connect(DB_DSN_SYNC)
    except Exception as e:  # noqa: BLE001 — 连不上不该崩,降级
        print(f"  ⚠️ --write-db 连库失败({type(e).__name__}: {e})— 跳过写库,结果只在上面控制台。"
              f"\n     查:Win .env 有 POKEMIR_DB_DSN_SYNC?Test-NetConnection 100.101.105.46 -Port 5432 通否?")
        return
    try:
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS replay_conservation (
                id serial PRIMARY KEY,
                run_session text NOT NULL,
                run_label text NOT NULL DEFAULT '',
                hand_index int,
                t0 double precision, t1 double precision,
                chip_movement double precision,
                pot double precision,
                status text,
                n_actions int,
                created_at timestamptz NOT NULL DEFAULT now()
            )""")
        cur.execute("DELETE FROM replay_conservation WHERE run_session=%s AND run_label=%s",
                    (sess, run_label))
        for r in records:
            cur.execute(
                """INSERT INTO replay_conservation
                   (run_session, run_label, hand_index, t0, t1, chip_movement, pot, status, n_actions)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (sess, run_label, r["hand_index"], r["t0"], r["t1"],
                 r["chip_movement"], r["pot"], r["status"], r["n_actions"]))
        con.commit()
        print(f"  ✅ --write-db:{len(records)} 手写入 replay_conservation "
              f"(run_session='{sess}', run_label='{run_label or '<空>'}')→ Claude 可直接 mcp 读")
    finally:
        con.close()


def load_card_marker_rois(profile_path):
    """→ {seat: card_marker ROI}。活跃集信号:头像左下两条红牌背(全玩家统一)= 在手。"""
    p = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    return {int(s["seat_index"]): s["card_marker"] for s in p.get("seats", []) if s.get("card_marker")}


def _avg_hash(crop):
    """8x8 average-hash → 64-bit int(感知哈希,测 ROI 视觉变化;不 OCR)。⚠️ Win-only(cv2)。"""
    import cv2
    if crop is None or crop.size == 0:
        return 0
    g = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (8, 8))
    m = float(g.mean())
    bits = 0
    for v in g.flatten():
        bits = (bits << 1) | (1 if float(v) > m else 0)
    return bits


def _hamming(a, b):
    return bin(a ^ b).count("1")


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


def build_truth_series(session, frames, stack_rois, amount_rois, button_rois, community_rois,
                       start, end, decimate, white_th=170, frac_th=0.12, card_marker_rois=None):
    """§19/T130/T132 --truth:一遍读 stack(+all-in) + 下注区 amount + D按钮(白占比,切手) + 公共牌。
    (去掉 win_amount OCR:+xx 跨桌不robust §19.11,切手改用 D 按钮亮度 §19.* / 省 8 次 OCR/帧)。"""
    import cv2
    from recognition.ocr import OCREngine
    ocr = OCREngine(gpu=True, name="replay")
    stack_series = {s: [] for s in stack_rois}
    bet_series = {s: [] for s in amount_rois}
    allin_marks = {s: [] for s in stack_rois}
    button_series = []  # (t, button_seat|None) — D 纯白高对比,白占比 argmax
    community_series = []
    marker_hash = {s: [] for s in (card_marker_rois or {})}  # P-6 活跃集:card_marker avg_hash
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
        best_s, best_wf = None, 0.0
        for s, (l, tt, w, h) in button_rois.items():
            wf = _white_frac(img[tt:tt + h, l:l + w], white_th)
            if wf > best_wf:
                best_s, best_wf = s, wf
        button_series.append((t, best_s if best_wf > frac_th else None))
        community_series.append((t, count_community(img, community_rois, white_th, frac_th)))
        if card_marker_rois:
            for s, (l, tt, w, h) in card_marker_rois.items():
                marker_hash[s].append((t, _avg_hash(img[tt:tt + h, l:l + w])))
    return stack_series, bet_series, button_series, community_series, allin_marks, marker_hash


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


def categorize_false_pos(false_pos, miss, sb=None, tol=2.0):
    """假阳归类(诊断求解器值不值得造):
      ① amount_misread:同手同街有未配对漏抓 → 同一动作金额读错(规则求解器修不了,要 OCR)
      ② sb_complete:preflop 且 ≈SB → SB 补齐半强制语义(可规则识别)
      ③ phantom:无对应漏抓的多余动作 → 真幻影(规则/守恒可拒,求解器主战场)
    返回 {bucket: [(hand,street,amt[,paired_miss])]}。"""
    miss_by_hs = {}
    for (h, st, amt) in miss:
        miss_by_hs.setdefault((h, st), []).append(amt)
    used = set()  # ((h,st), idx) 已配对的漏抓
    cats = {"amount_misread": [], "sb_complete": [], "phantom": []}
    for (h, st, amt) in false_pos:
        key = (h, st)
        avail = [k for k in range(len(miss_by_hs.get(key, []))) if (key, k) not in used]
        if avail:
            best = min(avail, key=lambda k: abs(miss_by_hs[key][k] - amt))
            used.add((key, best))
            cats["amount_misread"].append((h, st, amt, miss_by_hs[key][best]))
        elif st == "preflop" and sb and abs(amt - sb) <= tol + 1:
            cats["sb_complete"].append((h, st, amt))
        else:
            cats["phantom"].append((h, st, amt))
    return cats


def compare_per_hand(machine_hands, truth_path, tol=2.0):
    """A:按手对齐(无跨手巧合)→ recall + precision。
    machine_hands: [(stack_actions, bet_cands), ...] 按机器手序;truth 按 # hand 序 1:1 对齐。
    recall=真值被(stack增量∪下注区call-to)命中;precision=机器 stack 动作命中真值(假阳率=1-它)。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from compare_truth import parse_labels, CHIP_ACTIONS
    thands = parse_labels(Path(truth_path).read_text(encoding="utf-8"))
    n = len(thands)  # 只比到真值手数为止:机器多切的未标手忽略(不污染 precision;--end 可直接 9999)
    rows, miss, false_pos = [], [], []
    agg_true = agg_match = agg_machine = agg_prec = 0
    for i in range(n):
        stack_acts, bet_cands = machine_hands[i] if i < len(machine_hands) else ([], [])
        tchips = [(a.street, a.amount) for a in (thands[i].actions if i < len(thands) else [])
                  if a.type in CHIP_ACTIONS and a.amount is not None]
        # recall:truth(call-to 口径)匹配 机器 call-to(to_amount)∪ 增量 ∪ 下注区 call-to
        cands = ([(a.street, a.to_amount) for a in stack_acts]
                 + [(a.street, a.chips_in) for a in stack_acts] + list(bet_cands))
        used = [False] * len(cands)
        matched = 0
        for (st, amt) in tchips:
            for j, (cst, cv) in enumerate(cands):
                if not used[j] and cst == st and abs(cv - amt) <= tol:
                    used[j] = True
                    matched += 1
                    break
            else:
                miss.append((i + 1, st, amt))
        # precision:机器动作(按 call-to 口径=to_amount)匹配 truth
        macts = [(a.street, a.to_amount) for a in stack_acts]
        tused = [False] * len(tchips)
        prec = 0
        for (st, amt) in macts:
            for j, (tst, tv) in enumerate(tchips):
                if not tused[j] and tst == st and abs(tv - amt) <= tol:
                    tused[j] = True
                    prec += 1
                    break
            else:
                false_pos.append((i + 1, st, round(amt)))
        rows.append({"hand": i + 1, "n_true": len(tchips), "matched": matched, "n_machine": len(macts)})
        agg_true += len(tchips)
        agg_match += matched
        agg_machine += len(macts)
        agg_prec += prec
    return {"rows": rows, "agg_true": agg_true, "agg_match": agg_match,
            "agg_machine": agg_machine, "agg_match_prec": agg_prec,
            "per_hand_miss": miss, "per_hand_false": false_pos}


def main():
    ap = argparse.ArgumentParser(description="砖1b 回放重建 + 捕获率(T127)")
    ap.add_argument("--session"); ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--start", type=float, default=0); ap.add_argument("--end", type=float, default=1e9)
    ap.add_argument("--decimate", type=int, default=1, help="每 N 帧处理一帧(提速,默认全用)")
    ap.add_argument("--truth", help="真值文件(compare_truth 格式)")
    ap.add_argument("--sb", type=float, default=None); ap.add_argument("--bb", type=float, default=None)
    ap.add_argument("--ante", type=float, default=None); ap.add_argument("--pot", type=float, default=0)
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
    ap.add_argument("--dump-win", action="store_true",
                    help="T130:只读+打印每座 win_amount(+xx 结算)时间序列(验它能否当手边界,翻牌前结束的手也有)")
    ap.add_argument("--dump-signals", action="store_true",
                    help="T132:三信号切手验证 — D按钮移座(亮度) + win phash变化 + 公共牌reset 时间线对比(全程不OCR)")
    ap.add_argument("--dump-active", action="store_true",
                    help="活跃集验证:每座 card_marker(牌背)对标准参考 phash 的 hamming → 在手区间(不OCR);看在手 vs 弃/空 分不分得开")
    ap.add_argument("--marker-ref-seat", type=int, default=None,
                    help="(已弃用:改为每座对自己参考,此参数忽略)")
    ap.add_argument("--marker-ref-t", type=float, default=None,
                    help="--dump-active:参考帧时刻秒(默认首帧);挑该 ref-seat 明确在手的帧")
    ap.add_argument("--active-th", type=int, default=8,
                    help="--dump-active:hamming ≤ 此 = 像牌背参考=在手(默认8/64)")
    ap.add_argument("--active-at", default=None,
                    help="--dump-active:逗号分隔时刻(秒),打印各时刻的活跃集(座集合),对你眼标的街末名单做街级核")
    ap.add_argument("--capture-marker-refs", action="store_true",
                    help="从 --from-image 逐座算 card_marker phash → 写入 profile 每座 card_marker_ref(持久参考,脱离 ref-t)")
    ap.add_argument("--from-image", default=None,
                    help="--capture-marker-refs:全员在手的整桌截图(分辨率须与 profile 一致)")
    ap.add_argument("--hash-th", type=int, default=12,
                    help="--dump-signals:win phash 变化阈值(hamming>此=视觉突变,默认12/64)")
    ap.add_argument("--settle-win", type=float, default=2.0,
                    help="结算抑制(settle_guard):首个派彩 rise 前 N 秒起判结算噪声、抑制(锚真实结算点,非窗末;治 river settlement 假阳;0=关)")
    ap.add_argument("--solve", action="store_true",
                    help="砖2:reconstruct 后过守恒/合法性求解器(裁自加/重复读/全下后行动幻影 → 提 precision)")
    ap.add_argument("--conservation", action="store_true",
                    help="整手守恒对比:每手 chip_movement(Σ首稳态-Σ末稳态)+pot → 同口径 "
                         "v_hand_conservation 判 OK/CHECK/NULL,并排旧 DB 基准(21.3%%)→ 新桩基牢不牢的系统级数字。"
                         "无需 --truth;pot 取每手窗内 pot_size ROI 峰值(无则用 --pot)")
    ap.add_argument("--write-db", action="store_true",
                    help="把 --conservation 每手结果写进 VPS postgres 表 replay_conservation"
                         "(走 Win→VPS Tailscale 通路 / config.DB_DSN_SYNC)→ Claude 直接读,零粘贴")
    ap.add_argument("--run-label", default="",
                    help="--write-db 的运行标签(如 'solve+p6'),区分同 session 多次跑;同标签重跑覆盖")
    ap.add_argument("--p6", action="store_true",
                    help="P-6 加法:用 card_marker 活跃集补看不见的末位跟注(翻后仍活跃且未到注级→跟到注级);需 profile 有 card_marker_ref")
    ap.add_argument("--win-merge", type=float, default=6.0,
                    help="T130:+xx 结算聚类合并窗秒(大桌结算散开调大,如 12;默认 6)")
    ap.add_argument("--win-min", type=float, default=0.0,
                    help="T130:滤掉 < 此额的小额 +xx 噪声(某些桌 win_amount 误读 +3/+33;真结算远大于此,如 5/10/10 桌用 50)")
    args = ap.parse_args()

    if args.mock:
        _self_test()
        return
    if not args.session:
        ap.error("给 --session 或 --mock")

    profile_path = Path("rois") / f"{args.profile}.json"
    meta, frames = load_manifest(args.session)
    # 桌规级别:CLI 优先,否则读 manifest(record_frames 开录时存),再否则默认 2/4/4
    args.sb = args.sb if args.sb is not None else meta.get("sb", 2)
    args.bb = args.bb if args.bb is not None else meta.get("bb", 4)
    args.ante = args.ante if args.ante is not None else meta.get("ante", 4)
    print(f"  桌规:SB={args.sb} / BB={args.bb} / ante={args.ante}"
          f"{'(manifest)' if meta.get('sb') is not None else '(默认/CLI)'}")

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

    if args.dump_win:
        import cv2
        from recognition.ocr import OCREngine
        win_rois = load_win_rois(profile_path)
        _, community_rois = load_rois(profile_path)
        if not win_rois:
            ap.error("profile 无 win_amount ROI")
        ocr = OCREngine(gpu=True, name="replay")
        fdir = Path(args.session) / "frames"
        win_series = {s: [] for s in win_rois}
        community_series = []
        for k, (i, fn, t) in enumerate(frames):
            if t < args.start or t > args.end or (k % args.decimate):
                continue
            img = cv2.imread(str(fdir / fn))
            if img is None:
                continue
            for s, roi in win_rois.items():
                win_series[s].append((t, read_stack(img, roi, ocr)))
            community_series.append((t, count_community(img, community_rois, args.white_th, args.frac_th)))
        print(f"session {args.session}: {len(win_rois)} 座 win_amount ROI")
        print("\n=== 公共牌张数 over time(手边界参照)===")
        prev = None
        for (t, n) in community_series:
            if n != prev:
                print(f"  t{t:.1f}: {n} 张")
                prev = n
        print("\n=== 每座 win_amount(+xx 结算)出现时刻(按值变化折叠)===")
        all_win = []
        for s in sorted(win_series):
            ch = [(round(t, 1), int(v)) for (t, v) in collapse_changes(win_series[s]) if v is not None]
            nn = sum(1 for _, v in win_series[s] if v is not None)
            if ch or nn:
                print(f"  seat{s}: {nn}帧非空 | 出现: {ch}")
                all_win.extend(t for (t, _) in ch)
        print(f"\n=== 所有 +xx 出现时刻(排序;应≈每手末一簇)===\n  {sorted(set(all_win))}")
        print("\n判读:① +xx 是否每手末出现一次(含翻牌前结束的手)→ 能否当权威手边界;"
              "② 是否持久够中等帧抓到(非空帧数);③ 有无手内乱出/漏出。")
        return

    if args.dump_signals:
        import cv2
        btn_rois = load_button_rois(profile_path)
        win_rois = load_win_rois(profile_path)
        _, community_rois = load_rois(profile_path)
        fdir = Path(args.session) / "frames"
        btn_series = []          # (t, button_seat|None)
        win_prev, win_spikes = {}, {s: [] for s in win_rois}  # 每座 phash 突变时刻
        community_series = []
        for k, (i, fn, t) in enumerate(frames):
            if t < args.start or t > args.end or (k % args.decimate):
                continue
            img = cv2.imread(str(fdir / fn))
            if img is None:
                continue
            # D 按钮:白占比最高的座(纯白高对比)
            best_s, best_wf = None, 0.0
            for s, (l, tt, w, h) in btn_rois.items():
                wf = _white_frac(img[tt:tt + h, l:l + w], args.white_th)
                if wf > best_wf:
                    best_s, best_wf = s, wf
            btn_series.append((t, best_s if best_wf > args.frac_th else None))
            # win phash 突变
            for s, (l, tt, w, h) in win_rois.items():
                hsh = _avg_hash(img[tt:tt + h, l:l + w])
                if s in win_prev and _hamming(hsh, win_prev[s]) > args.hash_th:
                    win_spikes[s].append(round(t, 1))
                win_prev[s] = hsh
            community_series.append((t, count_community(img, community_rois, args.white_th, args.frac_th)))
        print(f"\n=== ① D 按钮移座(切手 start 信号)===")
        for (t, s) in collapse_changes(btn_series):
            print(f"  t{t:.1f}: button → seat{s}")
        print(f"\n=== ② win phash 突变时刻(切手 end / +xx 出现,不 OCR)===")
        allspk = sorted(t for ss in win_spikes.values() for t in ss)
        print(f"  各座: {[(s, win_spikes[s]) for s in sorted(win_spikes) if win_spikes[s]]}")
        print(f"  合并时刻: {sorted(set(allspk))}")
        print(f"\n=== ③ 公共牌 reset(→0)时刻 ===")
        resets, prev = [], None
        for (t, n) in community_series:
            if prev is not None and n == 0 and prev != 0:
                resets.append(round(t, 1))
            prev = n
        print(f"  {resets}")
        print("\n判读:三条时间线应**互相印证**(每手末≈一个 D移座 + 一个 win突变 + 一个公共牌reset);"
              "看哪条最干净、哪条补哪条的洞(D漏的same-seat / 公共牌漏的无翻牌手 / win的OCR无关因用phash)。")
        return

    if args.capture_marker_refs:
        import cv2
        if not args.from_image:
            print("ERROR: --capture-marker-refs 需 --from-image <全员在手整桌截图.png>")
            return
        img = cv2.imread(args.from_image)
        if img is None:
            print(f"ERROR: 读不到图 {args.from_image}")
            return
        p = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        captured = {}
        for s in p.get("seats", []):
            roi = s.get("card_marker")
            if not roi:
                continue
            l, tt, w, h = roi
            s["card_marker_ref"] = int(_avg_hash(img[tt:tt + h, l:l + w]))
            captured[s["seat_index"]] = s["card_marker_ref"]
        Path(profile_path).write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"已写 {len(captured)} 座 card_marker_ref → {profile_path}")
        print(f"  {captured}")
        print("\n下一步:跑 --dump-active(自动用存档参考)验 8 座仍 bimodal;某座全程低=该图它没在手 → 换图重采。")
        return

    if args.dump_active:
        import cv2
        marker_rois = load_card_marker_rois(profile_path)
        if not marker_rois:
            print("ERROR: profile 无 card_marker ROI — 先 roi_config --field seat_N --element card_marker 框")
            return
        fdir = Path(args.session) / "frames"
        per_seat_hash = {s: [] for s in marker_rois}      # {seat: [(t, avg_hash)]}(cv2,Win)
        btn_rois = load_button_rois(profile_path)          # 一键自动核 A:庄家必在手(白占比,不OCR)
        btn_series = []
        for k, (i, fn, t) in enumerate(frames):
            if t < args.start or t > args.end or (k % args.decimate):
                continue
            img = cv2.imread(str(fdir / fn))
            if img is None:
                continue
            for s, (l, tt, w, h) in marker_rois.items():
                per_seat_hash[s].append((t, _avg_hash(img[tt:tt + h, l:l + w])))
            best_s, best_wf = None, 0.0
            for s, (l, tt, w, h) in btn_rois.items():
                wf = _white_frac(img[tt:tt + h, l:l + w], args.white_th)
                if wf > best_wf:
                    best_s, best_wf = s, wf
            btn_series.append((t, best_s if best_wf > args.frac_th else None))
        # 参考:优先 profile 存档 card_marker_ref(持久,跨局通用);否则现取 ref-t(本局)
        pj = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        stored_ref = {int(s["seat_index"]): int(s["card_marker_ref"])
                      for s in pj.get("seats", []) if s.get("card_marker_ref") is not None}
        ref_t = args.marker_ref_t
        if ref_t is None:
            anyseat = min((s for s in per_seat_hash if per_seat_hash[s]), default=None)
            ref_t = per_seat_hash[anyseat][0][0] if anyseat is not None else 0.0
        print(f"\n=== 活跃集(card_marker 牌背,每座对自己参考,不OCR)===")
        src = "profile 存档 card_marker_ref(持久)" if stored_ref else f"现取 ref-t≈{ref_t:.1f}(本局)"
        print(f"  参考源:{src} | th={args.active_th}")
        per_seat_intervals = {}
        for s in sorted(per_seat_hash):
            if not per_seat_hash[s]:
                continue
            if s in stored_ref:
                ref_hash_s = stored_ref[s]
            else:
                ref_hash_s = min(per_seat_hash[s], key=lambda th: abs(th[0] - ref_t))[1]
            samples = [(t, _hamming(h, ref_hash_s)) for (t, h) in per_seat_hash[s]]
            hams = [hm for (_, hm) in samples]
            ivs = _active.active_intervals(samples, th=args.active_th, min_run=2)
            per_seat_intervals[s] = ivs
            med = sorted(hams)[len(hams) // 2]
            n_in = sum(1 for hm in hams if hm <= args.active_th)
            print(f"  seat{s}: hamming[min{min(hams)}/中{med}/max{max(hams)}] 在手{n_in}/{len(hams)}帧"
                  f" | 在手区间 {[(round(a, 1), round(b, 1)) for a, b in ivs]}")
        # 一键自动核 A(不OCR,零手工):庄家(按钮位)开局必在手 → 断言 dealer ∈ 活跃集
        starts = collapse_changes(btn_series)              # [(t, dealer_seat|None)]
        t_open = btn_series[0][0] if btn_series else 0.0   # 跳过录像开局 5s 内的"半截手"(非真发牌点)
        checks = [(t, d) for (t, d) in starts if d is not None and t > t_open + 5.0]
        if checks:
            print(f"\n  === 一键自动核 A:庄家开局必在手(不OCR)===")
            ok, bad = 0, []
            for (t, dealer) in checks:
                aset = _active.active_set_at(t + 2.0, per_seat_intervals)
                if dealer in aset:
                    ok += 1
                else:
                    bad.append((round(t, 1), dealer, sorted(aset)))
            for (t, d, aset) in bad:
                print(f"    ✗ hand@t{t} 庄家 seat{d} 在 t{t+2:.0f} 不在活跃集 {aset}")
            print(f"    庄家在手:{ok}/{len(checks)} 手 " + ("✅ 全过" if not bad else f"⚠ {len(bad)} 手矛盾(上列,看是真快弃还是漏检)"))
        if args.active_at:
            print(f"\n  === 指定时刻活跃集(街级核:对你眼标的街末名单)===")
            for tok in args.active_at.split(","):
                tok = tok.strip()
                if not tok:
                    continue
                t = float(tok)
                aset = sorted(_active.active_set_at(t, per_seat_intervals))
                print(f"    @t{t:.1f}: 活跃座 {aset}")
        print("\n判读:每座对自己参考 → 在手段 hamming≈0、弃/空/摊牌段高,应两极(bimodal)。")
        print("  ⚠️ 某座若【全程低】= 它在 ref-t 其实没在手(参考误取成弃/空态)→ 换 --marker-ref-t 到刚发牌帧。")
        print("  ⚠️ 摊牌时 marker 被显牌占 → 在手区间止于摊牌(下注街内有效)。")
        return

    stack_rois, community_rois = load_rois(profile_path)
    print(f"session {args.session}: {len(frames)} 帧, 时间窗 [{args.start},{args.end}], "
          f"{len(stack_rois)} 座 stack ROI")
    bet_series, hand_starts, marker_hash = {}, None, {}
    if args.truth:  # §19 融合 + T132 按钮切手:读下注区 + D 按钮(白占比)
        _, amount_rois = load_action_rois(profile_path)
        button_rois = load_button_rois(profile_path)
        cm_rois = load_card_marker_rois(profile_path) if args.p6 else None  # P-6 活跃集
        stack_series, bet_series, button_series, community_series, allin_marks, marker_hash = build_truth_series(
            args.session, frames, stack_rois, amount_rois, button_rois, community_rois,
            args.start, args.end, args.decimate, args.white_th, args.frac_th, cm_rois)
        hand_starts = _recon.hand_starts_from_button(button_series)
        print(f"  D 按钮移座 {len(hand_starts)} 次 → 按钮权威切手")
    else:
        button_rois = load_button_rois(profile_path)  # T138:守恒路径也用按钮权威切手
        stack_series, community_series, allin_marks, button_series = build_series_real(
            args.session, frames, stack_rois, community_rois, args.start, args.end, args.decimate,
            args.white_th, args.frac_th, button_rois)
        hand_starts = _recon.hand_starts_from_button(button_series) if button_series else None
        if hand_starts:
            print(f"  D 按钮移座 {len(hand_starts)} 次 → 按钮权威切手(守恒路径,治公共牌假reset切碎)")
        else:
            print("  ⚠️ 无按钮信号 → 回退公共牌/派彩切手(可能被假reset切碎)")

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
              "seats": list(stack_rois.keys()), "settle_guard": args.settle_win}
    if hand_starts:  # T132:D 按钮移座当权威切手边界
        config["hand_starts"] = hand_starts
    windows = _recon.segment_hands(stack_series, community_series, config)
    print(f"\n=== 手分段:检测到 {len(windows)} 手 ===")
    p6_ref = {}
    if args.p6 and marker_hash:                        # P-6:profile 存档 card_marker_ref
        pj6 = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        p6_ref = {int(s["seat_index"]): int(s["card_marker_ref"])
                  for s in pj6.get("seats", []) if s.get("card_marker_ref") is not None}
        if not p6_ref:
            print("  ⚠️ --p6 但 profile 无 card_marker_ref(先 --capture-marker-refs);跳过 P-6")
    # --conservation:读 pot_size ROI 每帧 → 每手取窗内峰值当 pot_size_final 代理
    cons_records, cons_pot_series = [], []
    if args.conservation:
        import cv2
        from recognition.ocr import OCREngine
        pot_roi, _ = load_pot_roi(profile_path)
        if pot_roi:
            ocr_pot = OCREngine(gpu=True, name="cons-pot")
            fdir = Path(args.session) / "frames"
            for k, (i, fn, t) in enumerate(frames):
                if t < args.start or t > args.end or (k % args.decimate):
                    continue
                img = cv2.imread(str(fdir / fn))
                if img is None:
                    continue
                v = read_stack(img, pot_roi, ocr_pot)
                if v is not None:
                    cons_pot_series.append((t, v))
            print(f"  --conservation:pot ROI 读到 {len(cons_pot_series)} 个非空(每手取窗内峰值)")
        else:
            print(f"  --conservation:profile 无 pot_size ROI → 退回 --pot={args.pot}(无则 NULL_POT)")

    all_actions, all_bet_cands, machine_hands = [], [], []
    for hi, (t0, t1) in enumerate(windows, 1):
        ss, cs = _recon.slice_series(stack_series, community_series, t0, t1)
        am = {s: [t for t in allin_marks.get(s, []) if t0 <= t < t1] for s in allin_marks}
        # 结算抑制已下沉到 reconstruct(锚首派彩 rise,非窗末 t1)→ config["settle_guard"]
        res = reconstruct(ss, cs, config, allin_marks=am)
        if args.solve:  # 砖2:合法性求解器裁幻影
            sr = _solver.solve_hand(res.actions, config)
            for a, why in sr.dropped:
                res.notes.append(f"求解器裁:seat{a.seat} {a.street} to{a.to_amount:.0f} @t{a.t:.1f} — {why}")
            res.actions = sr.kept
        if args.p6 and p6_ref:                         # P-6:活跃集补看不见的末位跟注
            ivs = {}
            for s, ser in marker_hash.items():
                if s not in p6_ref:
                    continue
                samples = [(t, _hamming(h, p6_ref[s])) for (t, h) in ser if t0 <= t < t1]
                ivs[s] = _active.active_intervals(samples, th=args.active_th, min_run=2)
            inferred, p6notes = _solver.infer_p6_calls(res.actions, ivs,
                                                       _recon.boundaries_from_community(cs), config)
            for n in p6notes:
                res.notes.append(n)
            res.actions.extend(inferred)
        all_actions.extend(res.actions)
        if args.conservation:
            cm = _cons.chip_movement_from_stacks(ss, fuse=_recon.fuse_plateaus)
            pot_win = [v for (t, v) in cons_pot_series if t0 <= t < t1]
            pot = max(pot_win) if pot_win else (args.pot or None)
            st = _cons.conservation_status(cm, pot)
            cons_records.append({"hand_index": hi, "t0": float(t0), "t1": float(t1),
                                 "chip_movement": float(cm),
                                 "pot": (float(pot) if pot is not None else None),
                                 "status": st, "n_actions": len(res.actions)})
            print(f"  [守恒] chip_movement={cm:.0f}  pot={pot}  → {st}")
        bet_cands = []
        if bet_series:  # §19 下注区 call-to 候选(融合比对用)
            bs = {s: [(t, v) for (t, v) in bet_series.get(s, []) if t0 <= t < t1] for s in bet_series}
            bet_cands = _recon.bet_callto_candidates(bs, cs, config)
            all_bet_cands.extend(bet_cands)
        machine_hands.append((res.actions, bet_cands))  # A:按手对齐用
        print(f"\n--- 手 {hi}  t[{t0:.1f},{t1:.1f}] ---")
        for a in sorted(res.actions, key=lambda x: x.t):
            print(f"  seat{a.seat} {a.street} {a.atype} 投入{a.chips_in:.0f} @t{a.t:.1f}")
        for n in res.notes:
            if any(k in n for k in ("涨", "ante", "胜率", "all-in", "求解器", "P-6")):  # 派彩/ante排除/all-in反解/求解器/P-6补
                print("  " + n)
    if args.conservation:
        print("\n" + _cons.format_comparison(
            _cons.summarize([(r["chip_movement"], r["pot"]) for r in cons_records])))
        print("  ⚠️ 守恒口径=整手对不对得平(底池锚),非逐动作 recall;后者要 --truth。"
              "\n  ⚠️ 单 session/单桌皮,样本=本次手数,不可外推全局。")
        if args.write_db:
            _write_conservation_db(cons_records, args.session, args.run_label)

    if args.truth:
        cmp = compare_to_truth(all_actions, args.truth)
        fus = compare_to_truth_fused(all_actions, all_bet_cands, args.truth)
        print(f"\n=== 捕获率(vs 真值,全局聚合 — 跨手巧合可能偏高)===")
        print(f"  真值筹码动作 {cmp['true']}")
        print(f"  ① stack-only 全局:  命中 {cmp['matched']} | {cmp['capture_rate']*100:.0f}%")
        print(f"  ② 融合全局:命中 {fus['matched']} | {fus['capture_rate']*100:.0f}%")
        # A:按手对齐(去跨手巧合)→ recall + 精度
        ph = compare_per_hand(machine_hands, args.truth)
        print(f"\n=== ⭐ A:按手对齐(无跨手巧合)===")
        summ = ", ".join(f"#{r['hand']}真{r['n_true']}/中{r['matched']}/机{r['n_machine']}"
                         for r in ph["rows"] if r["n_true"] or r["n_machine"])
        print(f"  每手: {summ}")
        rc = ph['agg_match'] / ph['agg_true'] if ph['agg_true'] else float('nan')
        pr = ph['agg_match_prec'] / ph['agg_machine'] if ph['agg_machine'] else float('nan')
        print(f"  ★ Recall(真值被抓): {ph['agg_match']}/{ph['agg_true']} = {rc*100:.0f}%")
        print(f"  ★ Precision(机器动作为真): {ph['agg_match_prec']}/{ph['agg_machine']} = {pr*100:.0f}%  ← 假阳率=1-此值")
        if ph['per_hand_miss']:
            print(f"  按手仍漏: {ph['per_hand_miss']}")
        if ph['per_hand_false']:
            print(f"  按手假阳(机器有/真值无): {ph['per_hand_false']}")
            # 假阳归类:量出 71% 里求解器够得着(phantom)vs 够不着(OCR金额错)
            cats = categorize_false_pos(ph['per_hand_false'], ph['per_hand_miss'], sb=args.sb, tol=2.0)
            nf = len(ph['per_hand_false'])
            print(f"\n  === 假阳归类(诊断:求解器值不值得造)===")
            print(f"  ① OCR金额读错(有对应漏抓,规则修不了→需OCR): {len(cats['amount_misread'])}/{nf}")
            for (h, st, amt, pm) in cats['amount_misread']:
                print(f"      手{h} {st}: 机器{amt} ↔ 真值{pm:.0f}(同一动作读偏)")
            print(f"  ② SB补齐半强制(规则可识别): {len(cats['sb_complete'])}/{nf}  {cats['sb_complete']}")
            print(f"  ③ 真幻影(无对应漏抓,规则/守恒可拒→求解器主战场): {len(cats['phantom'])}/{nf}")
            for (h, st, amt) in cats['phantom']:
                print(f"      手{h} {st}: {amt}")


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
