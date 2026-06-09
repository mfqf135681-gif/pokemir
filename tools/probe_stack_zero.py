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


def find_runs(seq, min_run):
    """seq=[(frame_num, value), ...] 升序 → value==0 的连续游程(允许 None 不打断?否:None 也打断,
    严格要求'稳定读出 0')。返回 [(start_fn, end_fn, length, last_nonzero_before)]。"""
    runs = []
    i, n = 0, len(seq)
    while i < n:
        if seq[i][1] == 0:
            j = i
            while j + 1 < n and seq[j + 1][1] == 0:
                j += 1
            length = j - i + 1
            if length >= min_run:
                # 归 0 前最后一个非 0 非 None 读数 = 全下额候选
                last_nz = next((v for (_, v) in reversed(seq[:i]) if v not in (0, None)), None)
                runs.append((seq[i][0], seq[j][0], length, last_nz))
            i = j + 1
        else:
            i += 1
    return runs


def main():
    ap = argparse.ArgumentParser(description="all-in stack→0 稳定信号可分性验")
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--roi-dir", default="./rois")
    ap.add_argument("--region", default="stack", help="profile 里 stack 区的 key(默认 stack)")
    ap.add_argument("--min-run", type=int, default=3, help="判稳定 0 的最小连续帧数(滤单帧误读)")
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
    log.info(f"扫 {len(files)} 帧 × {len(seats)} 座  区={args.region}  min-run={args.min_run}")

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

    # 每座读数分布 + 0-游程
    print(f"\n{'='*64}\n每座 stack 读数分布(n/座)+ 稳定 0-游程(min-run={args.min_run})\n{'='*64}")
    all_runs = {}
    for sidx in sorted(seats):
        seq = series[sidx]
        n = len(seq)
        n0 = sum(1 for _, v in seq if v == 0)
        nNone = sum(1 for _, v in seq if v is None)
        nNum = n - n0 - nNone
        runs = find_runs(seq, args.min_run)
        all_runs[sidx] = runs
        # 单帧 0(未进游程)= 疑误读
        single0 = n0 - sum(r[2] for r in runs)
        print(f"  s{sidx}: 总{n}  0读={n0}(进游程{n0-single0}/散单帧{single0})  None={nNone}  数字={nNum}  稳定0游程={len(runs)}")
        for (st, en, ln, lastnz) in runs:
            print(f"        ▶ 帧 {st}–{en}  长 {ln}  归0前最后一读(全下额候选)={lastnz}")

    total_runs = sum(len(r) for r in all_runs.values())
    print(f"\n合计稳定 0-游程 {total_runs} 次(= 候选 all-in)。对你的牌局看:漏抓?非全下座假阳?")

    if args.dump and total_runs:
        _fd = args.frames_dir.rstrip("/\\")
        tag = os.path.basename(os.path.dirname(_fd)) or os.path.basename(_fd) or "run"
        outdir = os.path.join("tools", "output", "stack_zero", tag)
        os.makedirs(outdir, exist_ok=True)
        for old in glob.glob(os.path.join(outdir, "*.png")):
            os.remove(old)
        # 帧号 → 路径(dump 首帧 stack 抠图)
        fn2fp = {fnum(fp): fp for fp in files}
        nwrote = 0
        for sidx, runs in all_runs.items():
            box = seats[sidx]
            for k, (st, en, ln, lastnz) in enumerate(runs, 1):
                fp = fn2fp.get(st)
                if not fp:
                    continue
                fr = cv2.imread(fp)
                if fr is None:
                    continue
                c = crop(fr, box)
                if c is None or c.size == 0:
                    continue
                ok = cv2.imwrite(os.path.join(outdir, f"s{sidx}_run{k}_len{ln}_amt{lastnz}_f{st}.png"),
                                 cv2.resize(c, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))
                nwrote += 1 if ok else 0
        print(f"dump 写了 {nwrote} 张(每游程首帧 stack 抠图)→ {outdir}\\")
        if nwrote == 0:
            log.warning("dump 0 张!检查路径/写权限")

    print("\n判读:真 all-in 应都落【长游程】;散单帧 0 = 误读(min-run 已滤);"
          "非全下座若冒长游程 = 假阳(看是否空座/busted → 靠 occupancy + 切手时机排)。")


if __name__ == "__main__":
    main()
