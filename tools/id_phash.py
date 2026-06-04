r"""tools/id_phash.py — 玩家 ID phash 验证(image-only,零 OCR)。

治 OCR 漂移把同玩家拆成多名字(见 find_player_aliases)。名字【像素】稳定、漂移的是 OCR
→ 对 id ROI 做 phash 当稳定 player key。本工具验两档(card_marker bimodal 同款思路):
  ① 同座跨帧稳定:同一玩家坐着不动 → phash 几乎不变(hamming≈0)。
  ② 跨座区分:不同玩家 → phash 两两 hamming 大(分得开)。
  ③ --refs:打各座众数 phash(int)供跨录像/跨座同玩家比对(ground truth 由用户指认)。

⚠️ cv2,Win 端。id ROI 须已跨座算齐(card_marker 锚),否则跨座 phash 必偏。
用法: python tools\id_phash.py --session data\recordings\<ts> [--hash-size 16] [--decimate 50]
"""
import argparse
import json
import os
from collections import Counter
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _phash(crop, size):
    import cv2
    if crop is None or crop.size == 0:
        return 0
    g = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (size, size))
    m = float(g.mean())
    bits = 0
    for v in g.flatten():
        bits = (bits << 1) | (1 if float(v) > m else 0)
    return bits


def _ham(a, b):
    return bin(a ^ b).count("1")


def main():
    ap = argparse.ArgumentParser(description="玩家 ID phash 验证(零 OCR)")
    ap.add_argument("--session", required=True)
    ap.add_argument("--profile", default="party_poker_8")
    ap.add_argument("--field", default="id", help="ROI 字段(默认 id;可 win_amount 等,验纹理/任意区)")
    ap.add_argument("--hash-size", type=int, default=16, help="phash 边长(16=256bit,名字细节够)")
    ap.add_argument("--decimate", type=int, default=50, help="每 N 帧采一次")
    ap.add_argument("--cluster-seats", default="",
                    help="把指定座(如 '1,7')phash 聚类 → 跨座比同玩家簇(治换座污染)")
    ap.add_argument("--cluster-th", type=int, default=15, help="聚类 hamming 阈(≤此同簇)")
    ap.add_argument("--cross", default="",
                    help="时序跨座比对 'L:R,...'(L前半在座↔R后半在座):用已知换座方向比同玩家")
    ap.add_argument("--frames", default="",
                    help="直接比指定帧+座 '文件:座,...':两两 phash hamming(零切半零众数,挑干净帧)")
    args = ap.parse_args()

    import cv2
    prof = json.loads((Path(_ROOT) / "rois" / f"{args.profile}.json").read_text(encoding="utf-8"))
    id_rois = {s["seat_index"]: s[args.field] for s in prof["seats"] if s.get(args.field)}
    fdir = Path(args.session) / "frames"
    mlines = (Path(args.session) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    frames = [json.loads(x) for x in mlines[1:] if x.strip()]
    bits = args.hash_size ** 2

    per_seat = {s: [] for s in id_rois}  # seat -> [hash,...]
    for k, d in enumerate(frames):
        if k % args.decimate:
            continue
        img = cv2.imread(str(fdir / d["file"]))
        if img is None:
            continue
        for s, roi in id_rois.items():
            l, t, w, h = roi
            per_seat[s].append(_phash(img[t:t + h, l:l + w], args.hash_size))

    # ── --frames "文件:座,文件:座":直接 phash 指定帧的指定座,两两 hamming(零切半零众数零猜)。
    if args.frames:
        import cv2
        items = []
        for tok in args.frames.split(","):
            fn, s = tok.split(":")
            s = int(s)
            img = cv2.imread(str(fdir / fn.strip()))
            if img is None:
                print(f"  读不到 {fn}"); continue
            l, t, w, h = id_rois[s]
            ph = _phash(img[t:t + h, l:l + w], args.hash_size)
            items.append((fn.strip(), s, ph))
            print(f"  {fn.strip()} s{s}: popcount={bin(ph).count('1')}")
        print("\n两两 hamming:")
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                fa, sa, pa = items[i]; fb, sb, pb = items[j]
                print(f"  {fa}s{sa} ↔ {fb}s{sb}: {_ham(pa, pb)}/{bits}")
        return

    # ── --cross "L:R,...":用已知时序比对同玩家(L 前半在座 ↔ R 后半在座)。治聚类撞共同态。
    if args.cross:
        def half_mode(seq, which):
            n = len(seq)
            half = seq[:n // 2] if which == "first" else seq[n // 2:]
            hs = [h for h in half if h]
            return Counter(hs).most_common(1)[0][0] if hs else 0

        print(f"\n=== --cross 时序比对(L前半在座 ↔ R后半在座, {bits}bit)===")
        first_modes = {}
        for pair in args.cross.split(","):
            L, R = (int(x) for x in pair.split(":"))
            mL = half_mode(per_seat.get(L, []), "first")
            mR = half_mode(per_seat.get(R, []), "second")
            first_modes[L] = mL
            print(f"  s{L}(前半) ↔ s{R}(后半): hamming={_ham(mL, mR)}/{bits}  "
                  f"popcount {bin(mL).count('1')}/{bin(mR).count('1')}")
        if len(first_modes) >= 2:
            ks = list(first_modes)
            print("\n  跨人区分(前半各玩家众数两两,应远):")
            for i in range(len(ks)):
                for j in range(i + 1, len(ks)):
                    print(f"    s{ks[i]} ↔ s{ks[j]}: {_ham(first_modes[ks[i]], first_modes[ks[j]])}")
        print("\n  判读:同人跨侧≈0-5成立 / 几十=跨侧名字框内位置不齐;跨人应远(几十)。")
        return

    # ── --cluster-seats "1,7":把指定座的 phash 按相似度聚类(治"换座"污染:
    #    一座会分出{你的名字, 空座}多簇)→ 再跨座比"同一玩家那簇"。
    if args.cluster_seats:
        seats = [int(x) for x in args.cluster_seats.split(",") if x.strip()]
        th = args.cluster_th

        def cluster(hashes):
            clu = []  # [[rep, count], ...]
            for h in hashes:
                for c in clu:
                    if _ham(h, c[0]) <= th:
                        c[1] += 1; break
                else:
                    clu.append([h, 1])
            return sorted(clu, key=lambda c: -c[1])

        print(f"\n=== 聚类(每{args.decimate}帧, hamming≤{th} 同簇)===")
        seat_clusters = {}
        for s in seats:
            hs = [h for h in per_seat.get(s, []) if h]
            cl = cluster(hs)
            seat_clusters[s] = cl
            print(f"  s{s}: {len(hs)}帧 → {len(cl)}簇")
            for i, (rep, cnt) in enumerate(cl):
                print(f"     簇{i}: {cnt}帧 popcount={bin(rep).count('1')} rep={rep}")
        if len(seats) == 2:
            a, b = seats
            print(f"\n=== 跨座比对 s{a} 各簇 ↔ s{b} 各簇(找同一玩家:最小 hamming)===")
            best = (999, None, None)
            for i, (ra, _) in enumerate(seat_clusters[a]):
                row = [f"s{b}簇{j}={_ham(ra, rb)}" for j, (rb, _) in enumerate(seat_clusters[b])]
                print(f"  s{a}簇{i}: " + "  ".join(row))
                for j, (rb, _) in enumerate(seat_clusters[b]):
                    if _ham(ra, rb) < best[0]:
                        best = (_ham(ra, rb), i, j)
            print(f"\n  最佳匹配: s{a}簇{best[1]} ↔ s{b}簇{best[2]} = {best[0]}/{bits}")
            print(f"  判读: 若这对≈0-5 = 同玩家跨座(含跨侧)成立;几十=跨侧名字框内位置不一致。")
        return

    print(f"=== ID phash 验证 ({args.hash_size}x{args.hash_size}={bits}bit, 每{args.decimate}帧)===")
    seat_mode = {}
    print("\n① 同座跨帧稳定(看是否有【稳定核】:多数帧贴众数、少数离群=空座/弃牌可滤):")
    for s in sorted(per_seat):
        hs = [h for h in per_seat[s] if h]
        if not hs:
            print(f"  s{s}: 无"); continue
        mode = Counter(hs).most_common(1)[0][0]
        seat_mode[s] = mode
        dists = sorted(_ham(h, mode) for h in hs)
        n = len(dists)
        med = dists[n // 2]
        p80 = dists[min(n - 1, int(n * 0.8))]
        tight = sum(1 for d in dists if d <= 10)  # 贴众数(≤10)的帧占比
        print(f"  s{s}: {n}帧 中位Δ={med} 80分位={p80} max={dists[-1]} | 贴众数(≤10)={tight}/{n}={tight*100//n}%")

    print("\n② 跨座区分(不同玩家→两两 hamming 应大;小=phash撞):")
    ss = sorted(seat_mode)
    pairs = [(ss[i], ss[j], _ham(seat_mode[ss[i]], seat_mode[ss[j]]))
             for i in range(len(ss)) for j in range(i + 1, len(ss))]
    if pairs:
        pairs.sort(key=lambda x: x[2])
        mn = pairs[0]
        print(f"  最小 hamming = {mn[2]}/{bits} (s{mn[0]}↔s{mn[1]});越大越好,接近0=有玩家撞")
        print(f"  最近3对: " + " ".join(f"s{a}↔s{b}:{h}" for a, b, h in pairs[:3]))

    print("\n③ 各座众数 phash(int,跨录像/跨座同玩家比对用):")
    for s in ss:
        print(f"  s{s}: {seat_mode[s]}")


if __name__ == "__main__":
    main()
