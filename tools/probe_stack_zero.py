r"""tools/probe_stack_zero.py — all-in 持久桩可分性验:stack→0 稳定信号(独立观测,非圈梁反推)。

用户实测(2026-06-09):all-in → 持有筹码(stack 区)【稳定归 0】,直到摊牌显牌型。
本探针验:stack 读数==0 的【持久游程】能否干净标 all-in ——
  ① 真 all-in = 长游程稳定 0(从全下到摊牌,几秒=多帧);
  ② 误读 = 单帧/极短 0(下一帧又读出数 → 持久性滤掉);
  ③ 空座/busted = 也可能 0/空(需 occupancy + 切手时机排,本探针先暴露、不排)。
→ 看真 all-in 是否都落长游程、有无非全下座产长游程假阳,定 min-run 阈 + 是否够格当桩。

stack 读数【复用 live 同一个 DigitReader.read()】(单一实现,防 harness/live 漂移
[[harness-vs-live-codepath-divergence]])。孤立 "0" digit_reader 读 0(EasyOCR 会丢成 None)。
⚠️ cv2 + 模板 + 帧,Win-only(本 Linux 无模板/cv2 不可跑)。

用法(Win):
  python tools\probe_stack_zero.py --frames-dir "data\recordings\<ts>\frames" --profile party_poker_8 --min-run 3 --dump
判读:每座 0-游程清单(起止帧/长度/归0前最后一读=全下额候选)对你的牌局看 ——
  真 all-in 都在长游程里?有没有非全下座冒长游程(假阳)?单帧 0 是不是都被 min-run 滤了?
"""
import argparse
import glob
import logging
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.digit_reader import DigitReader  # noqa: E402  单一实现:live 同款 stack 读

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("probe_stack_zero")


def load_stack_reader(profile, roi_dir):
    """复刻 orchestrator 载 stack DigitReader:优先 _stack.json,否则旧单文件。"""
    for name in (f"digit_templates_{profile}_stack.json", f"digit_templates_{profile}.json"):
        p = os.path.join(roi_dir, name)
        if os.path.isfile(p):
            log.info(f"stack 模板 ← {name}")
            return DigitReader.load(p)
    return None


def crop(frame, box):
    if not box or len(box) != 4:
        return None
    l, t, w, h = box
    H, W = frame.shape[:2]
    if l < 0 or t < 0 or l + w > W or t + h > H or w < 2 or h < 2:
        return None
    return frame[t:t + h, l:l + w]


def fnum(fp):
    import re
    m = re.search(r"(\d+)", os.path.basename(fp))
    return int(m.group(1)) if m else 0


def find_episodes(seq):
    """seq=[(frame_num, value:int|None)] 升序 → all-in 候选【事件】。
    事件 = 连续 (0 或 None) 且含 ≥1 个 0 的极大段,**遇到确认的正数(有筹码=非全下)才断**。
    → 同一手内被 None 切碎的 0 自动合并;跨手真多次(中间有筹码读数)自然分开。
    **不按长度过滤**(实证:s3 仅 10 帧也是真 all-in;靠 occupancy/时机去伪,不靠长短)。
    amount = 归 0 前【最后一次正数读】(= 推入前的 stack;与 reconstruct prior[-1] 一致)。
    单帧读偏 1(如 124 读成 123)是 digit-OCR 级问题,探针忠实报读到的值,不在此纠。
    返回 [{start, end, zeros, nones, amount}]。"""
    eps = []
    last_pos = None         # 归 0 前最后一次正数读
    i, n = 0, len(seq)
    while i < n:
        v = seq[i][1]
        if v is not None and v != 0:      # 确认筹码 → 打断 0/None 段
            last_pos = v
            i += 1
            continue
        j = i                              # 0/None 段
        while j + 1 < n and (seq[j + 1][1] == 0 or seq[j + 1][1] is None):
            j += 1
        zframes = [f for f, x in seq[i:j + 1] if x == 0]
        if zframes:                        # 含 ≥1 个 0 → 候选 all-in 事件
            eps.append({
                "start": zframes[0], "end": zframes[-1],
                "zeros": len(zframes),
                "nones": sum(1 for _, x in seq[i:j + 1] if x is None),
                "amount": last_pos,
            })
        i = j + 1
    return eps


def main():
    ap = argparse.ArgumentParser(description="all-in stack→0 稳定信号可分性验")
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--roi-dir", default="./rois")
    ap.add_argument("--region", default="stack", help="profile 里 stack 区的 key(默认 stack)")
    ap.add_argument("--mark-short", type=int, default=12, help="仅【标注】总 0 帧 < 此值的事件为'短'(不丢弃;实证短的也可能真)")
    ap.add_argument("--max-frames", type=int, default=20000)
    ap.add_argument("--dump", action="store_true", help="dump 每个 0-游程首帧 stack 抠图供眼验是不是真 0")
    args = ap.parse_args()

    prof_path = os.path.join(args.roi_dir, f"{args.profile}.json")
    if not os.path.isfile(prof_path):
        log.error(f"找不到 profile {prof_path}"); sys.exit(2)
    import json
    prof = json.load(open(prof_path, encoding="utf-8"))
    seats = {s["seat_index"]: s.get(args.region) for s in prof["seats"]}
    seats = {i: b for i, b in seats.items() if b}
    if not seats:
        log.error(f"profile 无 {args.region} 框"); sys.exit(2)

    reader = load_stack_reader(args.profile, args.roi_dir)
    if reader is None:
        log.error(f"找不到 stack 模板(digit_templates_{args.profile}[_stack].json)→ 无法复用 live 读法"); sys.exit(2)

    files = sorted(glob.glob(os.path.join(args.frames_dir, "*.png")))
    if not files:
        log.error(f"{args.frames_dir} 无 *.png"); sys.exit(2)
    if len(files) > args.max_frames:
        files = [files[i] for i in np.linspace(0, len(files) - 1, args.max_frames).astype(int)]
    log.info(f"扫 {len(files)} 帧 × {len(seats)} 座  区={args.region}  (事件合并:遇筹码数字才断)")

    # 每座时间序列 [(frame_num, value:int|None)]
    series = {s: [] for s in seats}
    for fp in files:
        frame = cv2.imread(fp)
        if frame is None:
            continue
        fn = fnum(fp)
        for sidx, box in seats.items():
            c = crop(frame, box)
            v = reader.read(c) if (c is not None and c.size) else None
            series[sidx].append((fn, v))

    # 每座读数分布 + all-in 事件(合并碎片)
    print(f"\n{'='*64}\n每座 stack 读数分布(n/座)+ all-in 事件(0/None 连续合并,遇筹码数字才断)\n{'='*64}")
    all_eps = {}
    for sidx in sorted(seats):
        seq = series[sidx]
        n = len(seq)
        n0 = sum(1 for _, v in seq if v == 0)
        nNone = sum(1 for _, v in seq if v is None)
        nNum = n - n0 - nNone
        eps = find_episodes(seq)
        all_eps[sidx] = eps
        print(f"  s{sidx}: 总{n}  0读={n0}  None={nNone}  数字={nNum}  all-in事件={len(eps)}")
        for e in eps:
            short = "  ⚠短" if e["zeros"] < args.mark_short else ""
            print(f"        ▶ {e['amount']} → 0   帧 {e['start']}–{e['end']}  (0帧×{e['zeros']}, None×{e['nones']}){short}")

    total = sum(len(e) for e in all_eps.values())
    print(f"\n合计 all-in 事件 {total} 次。对你的牌局看:漏抓?非全下座假阳(空座/busted)?金额准否?")

    if args.dump and total:
        _fd = args.frames_dir.rstrip("/\\")
        tag = os.path.basename(os.path.dirname(_fd)) or os.path.basename(_fd) or "run"
        outdir = os.path.join("tools", "output", "stack_zero", tag)
        os.makedirs(outdir, exist_ok=True)
        for old in glob.glob(os.path.join(outdir, "*.png")):
            os.remove(old)
        # 帧号 → 路径(dump 首帧 stack 抠图)
        fn2fp = {fnum(fp): fp for fp in files}
        nwrote = 0
        for sidx, eps in all_eps.items():
            box = seats[sidx]
            for k, e in enumerate(eps, 1):
                fp = fn2fp.get(e["start"])
                if not fp:
                    continue
                fr = cv2.imread(fp)
                if fr is None:
                    continue
                c = crop(fr, box)
                if c is None or c.size == 0:
                    continue
                ok = cv2.imwrite(os.path.join(outdir, f"s{sidx}_ep{k}_z{e['zeros']}_amt{e['amount']}_f{e['start']}.png"),
                                 cv2.resize(c, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))
                nwrote += 1 if ok else 0
        print(f"dump 写了 {nwrote} 张(每事件首个 0 帧的 stack 抠图)→ {outdir}\\")
        if nwrote == 0:
            log.warning("dump 0 张!检查路径/写权限")

    print("\n判读:每事件 = 一次 all-in 候选(碎片已合并)。不按长度过滤(短的也可能真,如 s3 实证);"
          "非全下座若冒事件 = 假阳(空座/busted → 靠 occupancy + 切手时机排,本探针不排只暴露)。")


if __name__ == "__main__":
    main()
