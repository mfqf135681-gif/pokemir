# amount 漏读 stack 跌幅兜底(step 1;治 last-actor 跟注短窗漏读)

## 1. 概述

step 1(amount 准度)收尾。live 实测 amount↔stack 跌幅 ~90-97% 自洽,残余漏读是**特定机制**:
**当前街最后一个行动者跟注**时,筹码一进池街就翻,下注区显示真额的时间窗极短 → 抓帧常卡在
该玩家自己盲注筹码(2/4)那一瞬 → 读成盲注而非真跟注额。用户翻牌谱钉死 2 例:
- 可乐(SB,强制下2)跟 allin 净投 **25**,下注区读 **2**;stack 跌幅读 **25**(对);
- 神啊(BB4)跟到34 净投 **30**,下注区读 **4**;stack 跌幅读 **30**(对)。

stack 跌幅(净投入)**不受该显示窗口影响** → 是兜底信号。

## 2. 改动

### pipeline/reconstruct.py — `reconcile_underread_amount`(纯逻辑,Linux 单测)
仅 call/bet/raise、stack 可信(>0):`stack_delta - amount >= margin(8)` **且** `stack_delta >= ratio(2)×amount`
→ 判下注区漏读,用 stack_delta 当 amount,返 reason;否则原样。
- ratio + margin 双护栏防 stack 噪声误兜(差<8 或 <2× 不动);
- raise-to 方向(amount≥stack_delta)天然不触发(差为负);
- 单测:漏读兜/一致不动/小差不兜/ratio护栏/stack空回退/非chip不动。

### pipeline/orchestrator.py — `_process_seat_actions` 接入
action_type override 后、raw_data 组装前调用;命中则 `event.amount = stack_delta` + log
`[amount兜底]` + raw_data 记 `amount_override_reason`(可审计)。

## 3. 为什么用 stack_delta 不破坏一致性

正常 call 的 amount 本就 == stack_delta(增量净投入,如 Gushen call r=26/s=26)。兜底改成
stack_delta = 维持同一约定(增量),与全库其余动作一致(非"加到X"总额)。

## 4. 验证

- Linux:`reconcile_underread_amount` 单测过(reconstruct 17 ✅);orchestrator/reconstruct
  `py_compile` OK;无循环 import。
- ⚠️ 整链路 live Win-only(盲点)。**验收**:重跑后 (a) 出现 `[amount兜底]` 日志在 last-actor
  跟注时;(b) DB `amount_override_reason` 非空的行 amount==stack_delta;(c) UNDERREAD 类清零。

## 5. 严限 / 回滚

- 始终生效(同 T16 action_type override 风格),不设闸门——清晰正确性修,且 raw_data 留痕可审计。
- 误兜风险:仅当 stack 噪声把跌幅抬到 >2×amount 且差≥8(stack 读很稳,概率极低;真误兜单点可被
  #226 求解器守恒兜回)。
- n=2 真值确认(用户牌谱);跨更多手验收看重跑。amount 不再是瓶颈(~97%→目标99%)。
