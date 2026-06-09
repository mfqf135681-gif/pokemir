r"""tools/probe_allin_yellow.py — all-in 黄色识别桩 离线可分性验(独立信号,圈梁只校不建)。

用户活观察(2026-06-09):all-in 后 fold_text 区出现【黄色粗体 "allin" 两字】(+头像换色闪烁边框)。
本探针验:fold_text 区【黄色像素计数】能否干净分出三类 ——
  ① 真 all-in(黄 "allin" 两字)   = 中等黄计数(落 [lo, hi] 带);
  ② 非 all-in(头像/弃牌白字/timer白字)= 黄计数 ≈ 0(白字非黄、普通头像非黄);
  ③ 黄头像(对抗 case,用户强调)   = 整框黄,巨量计数 → 上界 hi 拒。
→ 若 ①②③ 三簇离得开 + 上界 hi 能卡掉 ③,则 fold_text 黄计数([lo,hi] 双阈)= 可靠独立 all-in 桩。

黄色 HSV 参数 + 计数算法 **复用 +xx 的 probe_win_color.yellow_metrics**(单一实现,防 harness/live 漂移)。
区域用 **fold_text**(用户眼验:已框好、和 allin 区重叠、比 fold_area 紧 → 头像污染小)。
⚠️ cv2,Win-only(本 Linux 机无 cv2 不可跑;算法纯参数,Win 实测)。

用法(Win):
  python tools\probe_allin_yellow.py --frames-dir "data\recordings\<ts>\frames" --profile party_poker_8 --dump
判读:看②黄计数分布——真 all-in 那一簇离 0 多远(margin)、黄头像那一簇在哪(定 hi);
      dump 高计数抠图你眼标(真 allin / 黄头像 / 误触)→ 定 [lo, hi]。
"""
import argparse
import glob
import json
import logging
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 同目录 import probe_win_color

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("probe_allin_yellow")

# 单一实现:黄色计数复用 +xx 探针(同 HSV 参数 18/38/70/120,防漂移)
from probe_win_color import yellow_metrics  # noqa: E402


def white_count(bgr, smax, vmin):
    """白色像素数(低饱和 S<=smax & 高亮 V>=vmin)。clean 区(桌布非白)里白=动画。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return int(((hsv[..., 1] <= smax) & (hsv[..., 2] >= vmin)).sum())


def bright_count(bgr, vmin, exclude_green=False, glo=35, ghi=95, sfelt=40):
    """亮像素数(V>=vmin)——数据定阈主推:桌布永远暗(实测 V≤117),星星很亮(V≥200),
    一条 V>180 桌布命中 0、星星命中 16(放大图256)。色盲 → 白星/黄星通吃(治用户'白黄都有')。
    exclude_green=True:再排掉饱和绿(防极端亮绿误触),但桌布本就暗、一般不需要。clean 区用。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    V = hsv[..., 2]
    m = V >= vmin
    if exclude_green:
        H, S = hsv[..., 0], hsv[..., 1]
        m = m & ~((S >= sfelt) & (H >= glo) & (H <= ghi))
    return int(m.sum())


def nonfelt_count(bgr, vmin, glo, ghi, sfelt):
    """非桌布绿像素数:亮(V>=vmin)且【不是饱和绿】(S>=sfelt 且 H∈[glo,ghi]=桌布)。
    白星(低S→非绿)+ 黄星(H<glo→非绿)+ 白黄混合 全收;绿桌布全弃。
    比纯黄/纯白门稳——不挑星星具体色,只问'是不是桌布绿'。clean 区(只星星非绿)用。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    felt = (S >= sfelt) & (H >= glo) & (H <= ghi)
    return int(((V >= vmin) & (~felt)).sum())


def crop(frame, box):
    """box=[l,t,w,h] → 帧内安全裁剪;越界/过小 → None。"""
    if not box or len(box) != 4:
        return None
    l, t, w, h = box
    H, W = frame.shape[:2]
    if l < 0 or t < 0 or l + w > W or t + h > H or w < 2 or h < 2:
        return None
    return frame[t:t + h, l:l + w]


def hist_bar(counts, labels, width=44):
    mx = max(counts) or 1
    return "\n".join(f"  {lab:>10} | {'█' * int(round(width * c / mx))} {c}" for c, lab in zip(counts, labels))


def main():
    ap = argparse.ArgumentParser(description="all-in 黄色识别桩可分性验(fold_text 区黄计数)")
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--region", default="fold_text", help="扫哪个 ROI 区(默认 fold_text;可换 fold_area 对照)")
    ap.add_argument("--color", choices=["yellow", "white", "nonfelt", "bright"], default="yellow",
                    help="检测:yellow / white / nonfelt / bright(亮像素 V>=,数据定阈主推:桌布暗星星亮,白黄通吃)")
    ap.add_argument("--bright-vmin", type=int, default=180, help="bright:亮度下限 V>=(默认180;实测桌布≤117星星≥200)")
    ap.add_argument("--bright-exclude-green", action="store_true", help="bright:再排饱和绿(一般不需要,桌布本就暗)")
    ap.add_argument("--white-smax", type=int, default=50, help="白:饱和上限 S<=(默认50)")
    ap.add_argument("--white-vmin", type=int, default=180, help="白:亮度下限 V>=(默认180)")
    ap.add_argument("--nonfelt-vmin", type=int, default=110, help="nonfelt:像素亮度下限 V>=(排暗角)")
    ap.add_argument("--felt-hlo", type=int, default=35, help="桌布绿 H 下限")
    ap.add_argument("--felt-hhi", type=int, default=95, help="桌布绿 H 上限")
    ap.add_argument("--felt-smin", type=int, default=40, help="桌布绿 饱和下限(S>=才算绿)")
    ap.add_argument("--hlo", type=int, default=18, help="黄 H 下限(cv2 0-179)")
    ap.add_argument("--hhi", type=int, default=38)
    ap.add_argument("--smin", type=int, default=70)
    ap.add_argument("--vmin", type=int, default=120)
    ap.add_argument("--count-ths", default="20,50,100,200,400", help="黄计数阈(>判候选;定 [lo,hi] 用)")
    ap.add_argument("--lo", type=int, default=400, help="all-in 候选带下界(初定 400)")
    ap.add_argument("--hi", type=int, default=1100, help="all-in 候选带上界(>hi 疑黄头像)")
    ap.add_argument("--max-frames", type=int, default=600, help="全扫设大(如 20000)即不抽样")
    ap.add_argument("--dump", action="store_true", help="dump 抠图供眼标")
    ap.add_argument("--per-seat", type=int, default=0,
                    help="按座 dump 每座 N 张(高N/3+中N/3+低N/3),如 12→96张;0=旧的跨座 over/band/sub/zero")
    ap.add_argument("--dump-hits", action="store_true",
                    help="只 dump 抓到的 all-in(计数>=lo);同座连续帧合并为一次事件,只出峰值帧 → 几张图=报了几次,对牌局看漏没漏")
    ap.add_argument("--hit-gap", type=int, default=15,
                    help="--dump-hits:同座两次命中帧号间隔>此值=另一次 all-in 事件(默认15帧)")
    args = ap.parse_args()

    prof_path = os.path.join("rois", f"{args.profile}.json")
    if not os.path.isfile(prof_path):
        log.error(f"找不到 profile {prof_path}"); sys.exit(2)
    prof = json.load(open(prof_path, encoding="utf-8"))
    seats = {s["seat_index"]: s.get(args.region) for s in prof["seats"]}
    seats = {i: b for i, b in seats.items() if b}  # 只留有该区框的座
    if not seats:
        log.error(f"profile 里没有 {args.region} 框"); sys.exit(2)

    files = sorted(glob.glob(os.path.join(args.frames_dir, "*.png")))
    if not files:
        log.error(f"{args.frames_dir} 下没 *.png"); sys.exit(2)
    if len(files) > args.max_frames:
        files = [files[i] for i in np.linspace(0, len(files) - 1, args.max_frames).astype(int)]
    _cdesc = ({"white": f"白 S<={args.white_smax} V>={args.white_vmin}",
               "bright": f"亮 V>={args.bright_vmin}" + ("(排绿)" if args.bright_exclude_green else ""),
               "nonfelt": f"非绿 V>={args.nonfelt_vmin} 桌布绿H[{args.felt_hlo},{args.felt_hhi}]S>={args.felt_smin}"}
              .get(args.color, f"黄 H[{args.hlo},{args.hhi}] S>{args.smin} V>{args.vmin}"))
    log.info(f"扫 {len(files)} 帧 × {len(seats)} 座  区={args.region}  色={args.color}  {_cdesc}")

    counts = []                  # 全部黄计数
    by_seat = {}                 # seat -> [count]
    samples = []                 # (count, crop_img, fname, seat)
    for fp in files:
        frame = cv2.imread(fp)
        if frame is None:
            continue
        fn = os.path.basename(fp)[:-4]
        for sidx, box in seats.items():
            c = crop(frame, box)
            if c is None or c.size == 0:
                continue
            if args.color == "white":
                ycount = white_count(c, args.white_smax, args.white_vmin)
            elif args.color == "bright":
                ycount = bright_count(c, args.bright_vmin, args.bright_exclude_green,
                                      args.felt_hlo, args.felt_hhi, args.felt_smin)
            elif args.color == "nonfelt":
                ycount = nonfelt_count(c, args.nonfelt_vmin, args.felt_hlo, args.felt_hhi, args.felt_smin)
            else:
                _, ycount = yellow_metrics(c, args.hlo, args.hhi, args.smin, args.vmin)
            counts.append(ycount)
            by_seat.setdefault(sidx, []).append(ycount)
            if args.dump or args.dump_hits:
                # 只存元数据(计数/【原始完整路径fp】/座/框),不存图(存 crop=整帧 view→OOM)。
                # 存 fp 而非重拼路径:扫描时 fp 已读成功,dump 重读它必成(治路径重拼对不上→无图)。
                samples.append((ycount, fp, sidx, box))

    carr = np.array(counts)
    cths = [int(t) for t in args.count_ths.split(",")]
    cbins = [0, 5, 20, 50, 100, 200, 400, 700, 1200, 99999]
    ch, _ = np.histogram(carr, bins=cbins)
    cl = [f"{cbins[i]}-{cbins[i+1]}" for i in range(len(cbins) - 1)]
    print(f"\n{'='*62}\n黄计数分布 n={carr.size}(区={args.region};找 ①真allin簇 / ③黄头像簇 离 0 的位置)\n{'='*62}")
    print(hist_bar(ch.tolist(), cl))
    for th in cths:
        print(f"  @计数阈 {th}: 超阈占 {(carr > th).mean()*100:.2f}%")
    print("\n每座 黄计数 [min/中位/max](普通座大多≈0;某座 max 高=该座出现过 allin 或黄头像):")
    for s in sorted(by_seat):
        a = np.array(by_seat[s])
        print(f"  s{s}: [{int(a.min())}/{int(np.median(a))}/{int(a.max())}]")

    # 候选带统计(本次扫的关键判据)
    in_band = (carr >= args.lo) & (carr <= args.hi)
    over_hi = carr > args.hi
    sub = (carr >= 20) & (carr < args.lo)
    print(f"\n{'='*62}\n候选带判据(lo={args.lo} hi={args.hi})\n{'='*62}")
    print(f"  ① [lo,hi] 候选 all-in : {int(in_band.sum()):5d} 帧座 ({in_band.mean()*100:.2f}%)")
    print(f"  ② >hi 疑黄头像/超       : {int(over_hi.sum()):5d} 帧座 ({over_hi.mean()*100:.2f}%)  ← 应≈0,>0 必眼标")
    print(f"  ③ [20,lo) 阈下非零      : {int(sub.sum()):5d} 帧座 ({sub.mean()*100:.2f}%)  ← 看是否藏漏掉的 all-in")

    if (args.dump or args.dump_hits) and samples:
        # 每段录像一个子夹(从 frames-dir 推录像名)→ 跑多段不互相覆盖,可叠加对比。
        _fd = args.frames_dir.rstrip("/\\")
        tag = os.path.basename(os.path.dirname(_fd)) or os.path.basename(_fd) or "run"
        outdir = os.path.join("tools", "output", "allin_yellow", tag)
        os.makedirs(outdir, exist_ok=True)
        for old in glob.glob(os.path.join(outdir, "*.png")):  # 只清【本段】子夹,别段保留
            os.remove(old)

        import re

        def _fnum(fp):  # 从文件名抠帧号(f_010837 → 10837),供事件合并排序
            m = re.search(r"(\d+)", os.path.basename(fp))
            return int(m.group(1)) if m else 0

        to_dump = []  # (前缀, 计数, fp, box)
        if args.dump_hits:
            # 只 dump 命中(计数>=lo);同座连续帧(帧号间隔<=hit_gap)合并为一次 all-in 事件,出峰值帧。
            print(f"\n{'='*62}\n抓到的 all-in 事件(计数>={args.lo};同座连续帧合并)\n{'='*62}")
            total = 0
            for sidx in sorted(seats):
                hits = sorted(((_fnum(fp), yc, fp, box) for yc, fp, sx, box in samples
                               if sx == sidx and yc >= args.lo), key=lambda t: t[0])
                if not hits:
                    continue
                events, cur = [], [hits[0]]
                for h in hits[1:]:
                    if h[0] - cur[-1][0] <= args.hit_gap:
                        cur.append(h)
                    else:
                        events.append(cur); cur = [h]
                events.append(cur)
                peaks = [max(ev, key=lambda t: t[1]) for ev in events]  # 每事件峰值帧
                total += len(peaks)
                fr_list = ", ".join(f"f{fn}(y{yc},{len(ev)}帧)" for (fn, yc, _fp, _b), ev in zip(peaks, events))
                print(f"  s{sidx}: {len(peaks)} 次 → {fr_list}")
                for i, (fn, yc, fp, box) in enumerate(peaks, 1):
                    to_dump.append((f"s{sidx}_evt{i}", yc, fp, box))
            print(f"  合计 {total} 次 all-in 事件,共 dump {total} 张峰值帧 → 对牌局看漏没漏")
        elif args.per_seat:
            # 每座 N 张:高 k + 中 k + 低 k(k=N//3),文件名 s{座}_{hi/mid/lo}_ 便于逐座看
            k = max(1, args.per_seat // 3)
            for sidx in sorted(seats):
                ss = sorted((s for s in samples if s[2] == sidx), key=lambda t: t[0])  # 升序按计数
                if not ss:
                    continue
                nz = [s for s in ss if s[0] > 0]
                mid = [nz[i] for i in np.linspace(0, len(nz) - 1, min(k, len(nz))).astype(int)] if nz else []
                for band, grp in (("hi", ss[-k:]), ("mid", mid), ("lo", ss[:k])):
                    for yc, fp, _sx, box in grp:
                        to_dump.append((f"s{sidx}_{band}", yc, fp, box))
        else:
            def pick(lo, hi, n):  # [lo,hi) 计数带里按计数均匀取 n 个
                grp = sorted((s for s in samples if lo <= s[0] < hi), key=lambda t: t[0])
                if not grp:
                    return []
                idx = np.linspace(0, len(grp) - 1, min(n, len(grp))).astype(int)
                return [grp[i] for i in sorted(set(idx))]
            for tag, grp in (("over", pick(args.hi, 10**9, 12)), ("band", pick(args.lo, args.hi, 14)),
                             ("sub", pick(20, args.lo, 10)), ("zero", pick(0, 6, 6))):
                for yc, fp, sidx, box in grp:
                    to_dump.append((f"{tag}_s{sidx}", yc, fp, box))

        nwrote = 0
        for prefix, yc, fp, box in to_dump:
            fr = cv2.imread(fp)  # 直接重读原始路径(扫描时已读成功,必成)
            if fr is None:
                log.warning(f"dump 重读失败: {fp}"); continue
            c = crop(fr, box)
            if c is None or c.size == 0:
                continue
            fn = os.path.basename(fp)[:-4]
            ok = cv2.imwrite(os.path.join(outdir, f"{prefix}_y{yc:05d}_{fn}.png"),
                             cv2.resize(c, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))
            nwrote += 1 if ok else 0
        _mode = ("命中事件峰值帧(s{座}_evt{n})" if args.dump_hits
                 else f"每座{args.per_seat}张(高/中/低)" if args.per_seat else "跨座 over/band/sub/zero")
        print(f"\ndump 写了 {nwrote} 张 → {outdir}\\({_mode})")
        if nwrote == 0:
            log.warning("dump 0 张!检查路径/写权限")
    if args.dump_hits:
        print(f"\n判读:文件名 s{{座}}_evt{{第几次}}_y{{计数}}_{{帧号}};按帧号对你的牌局看每次 all-in 有没有漏。")
    else:
        print("\n判读:band 全 allin + over≈0(无黄头像)+ sub 无漏 → [lo,hi] 双阈跨录像成立,可接 live 桩。")


if __name__ == "__main__":
    main()
