# P2a:活跃集 silent-fold 救援(补 fold_ocr 漏读,准+简)

## 1. 概述 + 深读修正

P2 原计划"活跃集替 fold_ocr 铲 218ms"。**深读 `seat_fold_ocr` 发现它是多用途读**(fold + timer 兜底
+ all-in 关键词 + idle-baseline 喂摊牌检测),简单删会连带断 timer/all-in/摊牌 → "铲 218ms"过乐观。
**故 P2 拆**:P2a(准+简,安全,本次)做"活跃集救援 fold_ocr 漏的弃牌",**不删任何东西**;
P2b(快,−190ms,需先迁 timer/all-in/baseline 离 fold_area)另立、不做。

## 2. 改动(pipeline/orchestrator.py)

`_tick_active_set_and_blinds` 加 silent-fold 救援(承 P0 的 standing per-tick 活跃集):
- 累积 `_hand_dealt_seats`(本手见过牌的座);每座维护 `_seat_gone_ticks`(card_marker 连续消失帧)。
- 某座连续消失 → **救援为 silent fold**,四道安全闸:
  ① **仅 preflop/flop/turn**(river 摊牌亮牌也会让 card_marker 消失 → 留 2b 加 texture/showdown 护栏);
  ② **去抖 ≥2 帧**(灭 P1 实测 0.6% 闪读);
  ③ **未被 fold_ocr 标过**(`s not in _folded_seats`)才补 → 不重复;
  ④ 标 `_folded_seats` 后 `is_skippable` 让 `_process_seat_actions` 跳过该座 → **fold_ocr 不会双发 FOLD**。
- `_rescue_silent_fold`:标 _folded_seats + mirror FOLDED + 合成 FOLD action_event(玩家已知才落,
  street 由 normalizer 自动)+ emit `fold.activeset_rescue` 诊断。
- **fold_ocr 全程不动**(互补:它读到的照旧,活跃集只补它漏的)。仅 `_table_blinds` 模式跑。

## 3. 收益 / 不做什么

- **准**:捕到 fold_ocr 漏的 silent fold(P1 实测活跃集召回严格 > fold_ocr,8人桌每手多抓 2-3 个);
- **简**:silent-folded 座被正确 skip + 摊牌活跃数正确(_seats_with_events − _folded_seats);
- **不做**:不删 fold_ocr(留兜底+座级 A/B)、不碰 timer/all-in/摊牌(P2b)、不管 river(摊牌护栏 P2b)。

## 4. 验证

- Linux:`py_compile` OK;`SeatLifecycle` 已导入。逻辑自检:去抖/非river/去重/不双发/不伪造名/仅gated模式。
- ⚠️ cv2/live Win-only(救援逻辑在 cv2-adjacent 的 active-set handler 内,纯逻辑未抽单测)。
- **验收**(重跑):`fold.activeset_rescue` 诊断出现 → 逐条核(座/街合理、该座之后无矛盾动作、确实 fold_ocr 没标)
  = 座级 A/B 确认活跃集救援的是真 silent fold(补 P1 没做的座级证明)。

## 5. 严限 / 后续

- river silent fold 仍靠 fold_ocr(P2a 不管);摊牌护栏 + 把活跃集升为 fold 权威(替代非补充)=P2b/后续。
- 合成 FOLD 的 street 取 normalizer `_current_street`(community 处理在 active-set handler 前,通常对);
  诊断里另记 community-derived street 做对照。
- 收口 #228 的一部分(活跃集喂 fold);完整收口待 P2b。
