"""solver/endpoint_chain.py — 砖0:端点链清洗(圈梁 D23/D25/D26 = J-6/J-8 的料)。

把"每手每座的期初/期末栈"(识别层最硬的桩)串成跨手链,做三件事:
  ① 接缝分类:手 i 期末 vs 手 i+1 期初 的跳变 → CONTINUOUS / REBUY / SUSPECT_READ(#241 并入此处);
  ② 手内残差:每手 Σ(期末-期初) ≈ -rake → per-hand rake 候选 → per-table rake 基线(D25);
  ③ 端点法赢家归属(净额>0)+ 与 +xx 双信源核对(只标记不裁决,裁决归砖1)。

纯逻辑,无 DB/cv2;输入输出 plain dataclass/dict,Linux 全可测。
DB 取数与报告在 tools/solve_session.py(Win/DB 可达时跑)。

设计取舍(2026-06-12):
- 手内 net 用【同一手存下的 initial/final】,不用"下一手期初"(audit winner_check 旧法,
  会把接缝污染混进 net —— 归属 59% 的一部分死因);
- REBUY 判据 = gap > tol;整数倾向(整百/整五十)只作 round_hint 提示位,不参与定罪
  (圈梁纪律1:单线索不裁决,提示位供下游 2+ 印证引用);
- gap < -tol = SUSPECT_READ(正规局不许中途取筹下桌;按读数嫌疑标记,不猜成因);
- 全部输出仅【标记+追溯】,不改任何源数据(圈梁纪律 4/5)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 接缝分类常量
CONTINUOUS = "CONTINUOUS"        # |gap| ≤ tol:正常连续
REBUY = "REBUY"                  # gap > tol:两手之间补码/增购
SUSPECT_READ = "SUSPECT_READ"    # gap < -tol:筹码凭空减少 = 某端读数嫌疑
GAP_UNKNOWN = "GAP_UNKNOWN"      # 端点缺读,无法判
DUP_IN_HAND = "DUP_IN_HAND"      # 同名玩家在同一手出现两次(ID 重影,常见于半截首手)


@dataclass
class HandPoint:
    """一手里一名玩家的端点观测(全部来自识别层已存数据,本模块不推断不修改)。"""
    hand_id: str
    idx: int                     # 手在 session 内的序号(时间序)
    player: str
    initial: float | None        # 期初栈(ante 时刻 / raw_data.player_stacks_initial)
    final: float | None          # 期末栈(raw_data.player_stacks_final)
    win_xx: float | None = None  # +xx 显示的合法进账金额(无显示则 None)

    @property
    def net(self) -> float | None:
        """手内净额 = 期末 - 期初(端点法;不吃跨手接缝污染)。"""
        if self.initial is None or self.final is None:
            return None
        return self.final - self.initial


@dataclass
class Seam:
    """同一玩家相邻两手之间的接缝(中间缺席的手不在桌,语义不变)。"""
    player: str
    prev_hand: str
    next_hand: str
    prev_final: float | None
    next_initial: float | None
    gap: float | None            # next_initial - prev_final(正=凭空多,负=凭空少)
    kind: str = GAP_UNKNOWN
    round_hint: bool = False     # gap 整百/整五十(补码特征,仅提示)
    sources: list = field(default_factory=list)   # 追溯(圈梁纪律5)


def classify_seams(chain: list[HandPoint], tol: float = 6.0) -> list[Seam]:
    """① 接缝分类。chain = 同一玩家的 HandPoint 按 idx 升序。
    tol = 读数噪声容忍(实测 ante 时序噪声 ±4,留余量 6)。"""
    seams: list[Seam] = []
    for a, b in zip(chain, chain[1:]):
        s = Seam(player=a.player, prev_hand=a.hand_id, next_hand=b.hand_id,
                 prev_final=a.final, next_initial=b.initial, gap=None,
                 sources=[f"final@{a.hand_id[:8]}", f"initial@{b.hand_id[:8]}"])
        if a.hand_id == b.hand_id:
            # 同名同手两个端点 = ID 重影(首跑实数据:半截首手 时来运转转 占两座,
            # 自接缝 gap=-189 假嫌疑)。两点都不可信,标 DUP 不算 gap。
            s.kind = DUP_IN_HAND
            seams.append(s)
            continue
        if a.final is not None and b.initial is not None:
            s.gap = round(b.initial - a.final, 1)
            if abs(s.gap) <= tol:
                s.kind = CONTINUOUS
            elif s.gap > 0:
                s.kind = REBUY
                s.round_hint = (s.gap % 100 == 0) or (s.gap % 50 == 0)
            else:
                s.kind = SUSPECT_READ
        seams.append(s)
    return seams


OUTLIER_HAND = "OUTLIER_HAND"    # 链级:单手端点离群(前后两接缝反号互抵)


def pair_outliers(seams: list[Seam], cancel_frac: float = 0.5) -> list[dict]:
    """链级单点离群检测(2026-06-12 首跑实数据发现的签名):同一玩家相邻两条接缝
    反号且大致互抵(SUSPECT_READ 后跟 REBUY,|gap_i+gap_{i+1}| ≤ cancel_frac×max(|·|))
    = 夹在中间那一手的端点读偏了,不是真补码/真嫌疑(854366:-404→+552 实锤型)。

    只产出标记(中间手 hand_id + 证据),不改 seam 分类不改数据(圈梁纪律4/5);
    下游(rake 样本/归属/砖1)拿到标记自行决定剔除。"""
    out = []
    for a, b in zip(seams, seams[1:]):
        if a.player != b.player or a.next_hand != b.prev_hand:
            continue
        if a.gap is None or b.gap is None:
            continue
        opposite = (a.kind, b.kind) in ((SUSPECT_READ, REBUY), (REBUY, SUSPECT_READ))
        cancels = abs(a.gap + b.gap) <= cancel_frac * max(abs(a.gap), abs(b.gap))
        if opposite and cancels:
            out.append({"kind": OUTLIER_HAND, "player": a.player,
                        "hand": a.next_hand, "gap_in": a.gap, "gap_out": b.gap,
                        "sources": a.sources + b.sources})
    return out


FINAL_HEALED = "FINAL_HEALED"    # 本手某座 final 读坏(摊牌动画),用下一手 initial 回填


def heal_finals(by_player: dict[str, list[HandPoint]],
                pot_of: dict[str, float], tol: float = 6.0,
                rake_allow: float = 30.0) -> tuple[dict, list[dict]]:
    """链愈合(砖4,bafca031 实案):摊牌结算动画拍坏 final → 用【下一手 initial】回填。

    触发签名:某手 Σnet ≪ -(pot+rake)(钱凭空蒸发)——摊牌者 final 被读成假低值
    (bafca031:赢家 seat5 final 读 1、真 ~3900,net 假装 -3251)。
    回填:该手某玩家 net < -(pot+tol)(输得比整池还多=物理不可能)→ 取其下一手
    initial 当愈合后 final。
    🔒 守恒自校(2+印证 + 防 rebuy 污染):回填后该手 Σnet 必须落回
    [-(pot+rake_allow), tol] 才接受;否则回退原值(真 all-in 输光+下手 rebuy 会让
    Σnet 偏正 → 自动拒绝,不会抹掉真实输光)。

    返回 (healed_by_player, heal_records);原 HandPoint 不改(造新对象,纪律4)。
    """
    # 玩家 → {idx: 下一手 initial}
    next_init: dict[str, dict[int, float]] = {}
    for player, chain in by_player.items():
        ordered = sorted(chain, key=lambda x: x.idx)
        nxt = next_init.setdefault(player, {})
        for a, b in zip(ordered, ordered[1:]):
            if b.initial is not None:
                nxt[a.idx] = b.initial

    # 按手归组(用 hand_id)
    by_hand: dict[str, list[HandPoint]] = {}
    for chain in by_player.values():
        for p in chain:
            by_hand.setdefault(p.hand_id, []).append(p)

    overrides: dict[tuple[str, str], float] = {}   # (hand_id, player) → healed final
    records: list[dict] = []
    for hid, pts in by_hand.items():
        pot = pot_of.get(hid)
        if pot is None:
            continue
        # 逐座判:net < -(pot+tol) = 输得比整池还多 = 物理不可能 = final 坏读(触发签名)。
        # 不要求全手 Σnet 闭合 —— 弃牌座常无 final 读,Σ 永远凑不齐(real-data 教训:
        # 全手守恒带太严,bafca031 因死钱座缺 final 被拒)。改【逐座可信度】校验:
        # 回填后该座 net 必落 [-(pot+tol), pot+tol](赢不超整池、亏不超整池)= 由不可能
        # 变可信。next_init 是独立读(下一手开局快照)→ 2 印证:本手 final 坏 vs 下手 init 信。
        # rebuy 防护:rebuy 抬高 next_init → 回填 net 超 +pot → 出带自动拒(不抹真账)。
        for p in pts:
            if p.net is None or p.net >= -(pot + tol):
                continue   # 非"不可能亏",不碰(含真 all-in 输光:亏≤自身栈≤对手栈,通常≥-pot)
            ni = next_init.get(p.player, {}).get(p.idx)
            if ni is None or p.initial is None:
                continue   # 无下一手可回填 → 留 FINAL_SNAPSHOT_SUSPECT
            healed_net = ni - p.initial
            if -(pot + tol) <= healed_net <= pot + tol:
                overrides[(hid, p.player)] = ni
                records.append({"kind": FINAL_HEALED, "hand": hid, "player": p.player,
                                "healed_final": ni, "net_before": round(p.net, 1),
                                "net_after": round(healed_net, 1)})
    # 造愈合后的新链(原对象不动)
    healed_by_player: dict[str, list[HandPoint]] = {}
    for player, chain in by_player.items():
        new_chain = []
        for p in chain:
            ov = overrides.get((p.hand_id, p.player))
            if ov is not None:
                new_chain.append(HandPoint(hand_id=p.hand_id, idx=p.idx, player=p.player,
                                           initial=p.initial, final=ov, win_xx=p.win_xx))
            else:
                new_chain.append(p)
        healed_by_player[player] = new_chain
    return healed_by_player, records


def hand_residuals(hands_points: dict[str, list[HandPoint]],
                   tol_seat: float = 6.0, rake_cap: float = 500.0) -> dict:
    """② 手内残差:每手 Σnet ≈ -rake(钱只能流向 rake)。

    返回 {hand_id: {"sum_net", "n_seats", "n_missing", "rake_candidate"}}。
    缺座的手 Σnet 不可当 rake(缺座的钱去向不明)→ rake_candidate=False(不强推)。"""
    out = {}
    for hid, pts in hands_points.items():
        nets = [p.net for p in pts]
        missing = sum(1 for n in nets if n is None)
        s = round(sum(n for n in nets if n is not None), 1)
        out[hid] = {"sum_net": s, "n_seats": len(pts), "n_missing": missing,
                    "rake_candidate": missing == 0 and -rake_cap <= s <= tol_seat}
    return out


def rake_baseline(residuals: dict, min_samples: int = 20) -> dict | None:
    """③ per-table rake 基线(D25)。样本不足 → None(冷启动期下游用保守容忍,不硬给数)。"""
    vals = sorted(-r["sum_net"] for r in residuals.values()
                  if r["rake_candidate"] and r["sum_net"] <= 0)
    if len(vals) < min_samples:
        return None
    return {"n": len(vals), "median": vals[len(vals) // 2],
            "p90": vals[int(len(vals) * 0.9)]}


def attribute_winners(pts: list[HandPoint], pot: float | None, tol: float = 6.0) -> dict:
    """端点法赢家归属(同一手所有玩家的 HandPoint)。

    返回 {"winners": [player], "xx_winners": [player], "agree_xx": bool|None, "flags": [...]}。
    纪律1:端点与 +xx 不一致只标记,不裁决(裁决归砖1逐手求解);
    赢额合计 > pot(+容忍)= rebuy/误读混入 → flag,供 seam 解释。"""
    winners = sorted(p.player for p in pts if p.net is not None and p.net > tol)
    xx_winners = sorted(p.player for p in pts if p.win_xx is not None)
    flags = []
    if pot is not None and winners:
        total_won = sum(p.net for p in pts if p.net is not None and p.net > tol)
        if total_won > pot + tol:
            flags.append("won_exceeds_pot")
    agree = (winners == xx_winners) if (winners or xx_winners) else None
    return {"winners": winners, "xx_winners": xx_winners, "agree_xx": agree, "flags": flags}
