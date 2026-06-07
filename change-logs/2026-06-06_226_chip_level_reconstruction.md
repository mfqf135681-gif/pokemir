# #226 筹码级/赢家重建(端点守恒,实装第一步)

## 1. 概述

四步法。#226 守恒求解器第一刀:**筹码级 + 赢家重建**(序列级二期)。依据本会话实测——
per-hand stack 端点(`player_stacks_initial`/`final`,全座)是可靠桩(24/25 手守恒到 rake 级),
per-action 读取噪声(~53% 筹码丢失)**不影响此层**。深读确认:`_capture_seat_stacks` 已在
hand-start/end 抓全座端点(信息饥饿:不重建);缺的是**消费它做重建**。

## 2. 改动

### pipeline/reconstruct.py — `reconstruct_hand_chips`(纯逻辑,Linux 单测)
端点字典 → 每手 per-seat 净额 + 赢家 + rake。**只可靠定**:净额(final−initial)、赢家(净>0)、
rake(=−Σ净)、输家投入(=净损,精确);**赢家投入/绝对底池端点定不了**(winner_contrib∈[0,初始])
→ pot 读仅交叉验证 + sanity(pot > 2.5×输家投入 → `pot_suspect`)。守恒/异常 flag:rebuy(Σnet大正)、
excess_loss、no_winner、split/multi_winner、partial_seats。
**4 手实测单测**:24f5(净额/赢家/rake精确,pot合理)· 5e60(pot超读被flag,净额仍对)·
0809(纠动作捕获的错赢家:真赢家 s5 非 BB)· rebuy(Σnet大正→conservation_ok False)。

### pipeline/orchestrator.py — `_end_current_hand` 接入
存 `player_stacks_final` 后调 `reconstruct_hand_chips`(init/final + blind_level),结果存
`raw_data["chip_reconstruction"]`,log `[#226重建] 赢家/净额/rake/flags`。键 int 强转防 jsonb 串化。

## 3. 价值

- **每手 per-player 净额 + 赢家** = profiling(胜率/盈亏)的可靠地基,且**纠正动作捕获的错**
  (0809 动作捕获记 BB 赢、端点证 s5 赢);
- per-action 噪声此层无关(端点兜);
- pot 超读被 sanity 自动标(5e60/8f0b)。

## 4. 验证

- Linux:`reconstruct_hand_chips` 单测过(reconstruct 19 ✅);全文件 `py_compile` OK。
- ⚠️ 接入路径(cv2/live)Win 验。**验收**:重跑后 DB `raw_data->'chip_reconstruction'` 出现,
  赢家/净额与实际一致(尤其纠错赢家)、pot_suspect 标在超读手、rebuy 标在买入手。

## 5. 严限 / 后续

- **仅筹码级/赢家**(净额、赢家、rake);**动作序列级**(哪条街/顺序)是 #226 二期(端点定总额 +
  底池进度/活跃集fold时序/下注区phash变化时刻 定序)。
- rake 常数(~12)待确认真rake vs 系统偏移;rebuy 仅 flag 未接入校正(_infer_insurance 雏形)。
- 单 session ~25 手,待稳态长局扩样本。

### 二期前向引擎候选:PokerKit(context7 实证,2026-06-06)
**uoftcprg/PokerKit**(同行评审、纯 Python、**Linux 可测**)——序列重建的"现成锁芯",
省掉手写德州规则/边池/rake(后两者手写=bug 磁石)。当时查到的 4 原语:
- `can_complete_bet_or_raise_to(amount)` = **合法性 oracle**(min-raise/max 合法区间,反向搜索要的)
- state 机 + `check_or_call`/`fold` + `state.stacks`/`status` = **前向引擎**(喂候选序列→算 pot/stack/轮到谁)
- `state.pot_amounts` = 主池 + **各边池**(all-in 必需)
- `rake()` 内建可自定义(%+cap+no-flop-no-drop+自定义函数)→ 对口 "rake ~12" 漏点;摸清 WePoker rake 规则后可显式建模

**定性(别当银弹)**:它治**逻辑半边,而逻辑不是瓶颈**(脑子够、1Hz 的眼睛是命门)。
**必要不充分**——它判合法/算准 pot,但"实际哪条序列"仍靠捕获约束(指针顺序/底池增量/fold时点)坍缩;
多人多次加注手剪枝后仍可能多条。**不改变"先测捕获、再建序列"的顺序**,只确定真要建时引擎不用自造。
接时需对表 WePoker ante/straddle → PokerKit 牌局定义(NLHE 应无碍)。
