"""pipeline/clean_window.py — 阶段0 干净窗捕获:纯逻辑核(状态机 + 多帧共识)。

设计 doc: requirement-discussions/主题-主程序架构重构.md → 《执行排程》阶段 0 / §B。

背景(实测,2026-06-23):
  - 总底池 latch(结算)→ 按钮移动(新手)之间有 ~5s/≈70tick 干净窗(中位 5.05s,
    p10-p90 4.5-5.8s,414 手,方差极小=WePoker 固定结算展示时长)。
  - 当前 orchestrator 在【按钮那一刻】单帧抓 final/initial 端点 + 玩家ID —— 恰是上手
    overlay 未散的脏时刻(实测端点 recon 干净仅 31%、98% 的"rebuy"是非圆整读取假象、
    TempUser 2741/2d)。
  - 干净窗洞察:窗内 stacks 已结算定稿 → 同一份干净读【既是上手 final 也是下手 initial】。

本模块把"窗内多帧 + 中位(stack)/众数(id)"做成**纯逻辑**:无截屏、无时钟、无 DB。
调用方每 tick 喂读数,本模块决定何时累积、产出共识。接线进 orchestrator(采集时序)
= 冻结范围 R-5,留 §5 解冻 case;本文件是新建解释/聚合件,冻结豁免。

B 域不变量关联(蓝图 B.2/B.3):
  - B-21 端点锚是承重锚 → 窗内中位治单帧误读;
  - B-28 玩家ID 多帧众数(并列取最长,同 player-id 配方);
  - 无结算手回退:实测 ~11/705 手 fold 到底无总底池 latch → fallback,调用方走单帧兜底
    (=当前行为),不漏手。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass
class CleanWindowResult:
    """窗内共识产出。stacks 同时供 上手 final / 下手 initial 两个端点。"""
    stacks: dict[int, float]        # per-seat 端点共识(中位)
    ids: dict[int, str]             # per-seat ID 共识(众数,并列取最长)
    sample_counts: dict[int, int]   # per-seat stack 累积帧数(覆盖/置信信号)
    fallback: bool                  # True=本手无结算窗 → 调用方走单帧兜底
    n_ticks: int                    # 窗内累积的 tick 数


def median(xs: list[float | None]) -> float | None:
    """中位数(忽略 None);空→None。偶数个取中间两数均值。"""
    vals = sorted(v for v in xs if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def mode_longest(xs: list[str]) -> str | None:
    """众数(忽略空串);并列时取最长(多字符=OCR 捕获更全,同 player-id 配方 B-28)。"""
    vals = [v for v in xs if v]
    if not vals:
        return None
    counts = Counter(vals)
    top = max(counts.values())
    cands = [v for v, c in counts.items() if c == top]
    return max(cands, key=len)


class CleanWindowCapture:
    """每手一实例(或 reset 复用)的干净窗累积器。纯状态机,无副作用。

    时序(调用方):
        cw = CleanWindowCapture()
        # 每 tick(本手进行中)——settled = orchestrator._pot_label_latched:
        cw.tick(settled, stacks={seat: stack|None}, ids={seat: name|''})
        # 按钮移动(换手)时:
        result = cw.finalize()      # CleanWindowResult
        cw.reset()                  # 进入下一手

    语义:首次 settled=True 进入窗口并 latch(此后 settled 即便回 False 也保持在窗,
    因 _pot_label_latched 本手不会撤);窗内每 tick 累积该 tick 的非空读数。
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._in_window: bool = False
        self._stacks: dict[int, list[float]] = {}
        self._ids: dict[int, list[str]] = {}
        self._n_ticks: int = 0

    @property
    def in_window(self) -> bool:
        return self._in_window

    def tick(
        self,
        settled: bool,
        stacks: dict[int, float | None] | None = None,
        ids: dict[int, str] | None = None,
    ) -> None:
        """每 tick 调用。settled=本手是否已进结算(总底池 latch)。
        一旦进窗,累积该 tick 的非空 stack/id 读数(多帧)。进窗前的 tick 全忽略。"""
        if settled:
            self._in_window = True
        if not self._in_window:
            return
        self._n_ticks += 1
        if stacks:
            for s, v in stacks.items():
                if v is not None:
                    self._stacks.setdefault(s, []).append(float(v))
        if ids:
            for s, name in ids.items():
                if name:
                    self._ids.setdefault(s, []).append(name)

    def finalize(self) -> CleanWindowResult:
        """按钮移动(换手)时调用,产出共识端点 + ID。
        从未进窗(无结算信号)→ fallback=True,调用方走单帧兜底(当前行为),不漏手。"""
        if not self._in_window:
            return CleanWindowResult(
                stacks={}, ids={}, sample_counts={}, fallback=True, n_ticks=0
            )
        stacks: dict[int, float] = {}
        for s, xs in self._stacks.items():
            m = median(xs)
            if m is not None:
                stacks[s] = m
        ids: dict[int, str] = {}
        for s, xs in self._ids.items():
            m = mode_longest(xs)
            if m is not None:
                ids[s] = m
        counts = {s: len(xs) for s, xs in self._stacks.items()}
        return CleanWindowResult(
            stacks=stacks, ids=ids, sample_counts=counts,
            fallback=False, n_ticks=self._n_ticks,
        )
