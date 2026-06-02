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


def count_community(img, community_rois, cardrec):
    """数有几张公共牌(CNN recognize_single 非 None)。⚠️ Win-only。"""
    n = 0
    for (l, t, w, h) in community_rois:
        crop = img[t:t + h, l:l + w]
        try:
            if cardrec.recognize_single(crop):
                n += 1
        except Exception:
            pass
    return n


def build_series_real(session, frames, stack_rois, community_rois, start, end, decimate):
    """Win 路径:逐帧读 → stack_series / community_series。"""
    import cv2
    from recognition.cards import CardRecognizer
    from recognition.ocr import OCREngine
    ocr = OCREngine(gpu=True, name="replay")
    cardrec = CardRecognizer()
    stack_series = {s: [] for s in stack_rois}
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
        community_series.append((t, count_community(img, community_rois, cardrec)))
    return stack_series, community_series


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


def main():
    ap = argparse.ArgumentParser(description="砖1b 回放重建 + 捕获率(T127)")
    ap.add_argument("--session"); ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--start", type=float, default=0); ap.add_argument("--end", type=float, default=1e9)
    ap.add_argument("--decimate", type=int, default=1, help="每 N 帧处理一帧(提速,默认全用)")
    ap.add_argument("--truth", help="真值文件(compare_truth 格式)")
    ap.add_argument("--sb", type=float, default=2); ap.add_argument("--bb", type=float, default=4)
    ap.add_argument("--ante", type=float, default=4); ap.add_argument("--pot", type=float, default=0)
    ap.add_argument("--mock", action="store_true", help="离线自测核(不读帧)")
    ap.add_argument("--dump-stacks", action="store_true",
                    help="只读+打印每座 stack 轨迹(验读取质量,不需真值/不跑 reconstruct)")
    args = ap.parse_args()

    if args.mock:
        _self_test()
        return
    if not args.session:
        ap.error("给 --session 或 --mock")

    stack_rois, community_rois = load_rois(Path("rois") / f"{args.profile}.json")
    meta, frames = load_manifest(args.session)
    print(f"session {args.session}: {len(frames)} 帧, 时间窗 [{args.start},{args.end}], "
          f"{len(stack_rois)} 座 stack ROI")
    stack_series, community_series = build_series_real(
        args.session, frames, stack_rois, community_rois, args.start, args.end, args.decimate)

    if args.dump_stacks:
        print("\n=== 每座 stack 轨迹(验读取质量)===")
        for s in sorted(stack_series):
            obs = stack_series[s]
            nn = sum(1 for _, v in obs if v is not None)
            plats = _recon.fuse_plateaus(obs)
            print(f"seat{s}: {len(obs)}读/{nn}非空 | 平台 {[(round(t, 1), int(v)) for t, v in plats]}")
        print("\n判读:平台应是【单调下降的合理筹码值】(每个台阶=一次投入)。"
              "\n  全 None/乱跳/读不出 → 读取层(OCR/ROI)有问题,先修这个(=砖2 数字CNN要解的)。"
              "\n  台阶干净 → 架构弱环(读取)过关,reconstruct(已自测)能接上。")
        return

    config = {"sb": args.sb, "bb": args.bb, "ante": args.ante, "pot": args.pot,
              "seats": list(stack_rois.keys())}
    res = reconstruct(stack_series, community_series, config)
    print("\n重建动作:")
    for a in sorted(res.actions, key=lambda x: x.t):
        print(f"  seat{a.seat} {a.street} {a.atype} 投入{a.chips_in:.0f} @t{a.t:.1f}")
    for n in res.notes:
        print("  " + n)
    if args.truth:
        cmp = compare_to_truth(res.actions, args.truth)
        print(f"\n=== 捕获率(vs 真值)===\n  真值筹码动作 {cmp['true']} | 重建命中 {cmp['matched']} "
              f"| 捕获率 {cmp['capture_rate']*100:.0f}%")
        if cmp["missed"]:
            print("  漏:", cmp["missed"])
        if cmp["extra"]:
            print("  多/假:", cmp["extra"])


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


if __name__ == "__main__":
    main()

# ── [WIN 验证 CHECKLIST] ──────────────────────────────────────────────
#  1. 先有砖1b 录制(record_frames mss 窗口帧)+ 该段一手的 history 真值文件
#  2. 从你标注的"第几分钟"定 --start/--end(秒,manifest t_mono)圈出那一手
#  3. 跑:--session ... --start S --end E --truth hand.txt --pot <该手底池> --ante 4
#  4. 看"重建动作" + "捕获率" → 这是 §15 架构在真实数据上的首个系统级数字
#  ⚠️ read_stack/count_community 我 Linux 没验;若 stack 读全 None / 公共牌数不对,
#     先单帧 debug 这两个适配器(ROI 对齐 / allowlist / CNN 输入)。
