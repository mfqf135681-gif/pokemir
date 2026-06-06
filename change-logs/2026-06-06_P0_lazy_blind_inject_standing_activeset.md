# P0:惰性盲注注入 + standing per-tick 活跃集(治 ~19% 漏派 + 喂 P1 fold 数据)

## 1. 概述

实战数据(满桌局)抓到:`BUTTON_CUT` 一移座就 `_start_new_hand`+ 立即注入,但**新手发牌动画
未完成、card_marker 未显示 → 活跃集读空 → 盲注/ante 漏派**,实测 ~3/16 手(~19%,含 `active=[]`)。

深读 `_tick` 后修正原计划:"读到注入为止"的活跃集只读到翻前早期,**拿不到手中弃牌的 mid-hand
转移**(P1 fold 验证所需)。故 P0 做成 **standing per-tick 活跃集读**(整手,<1ms/tick),一举两用。

## 2. 改动(pipeline/orchestrator.py)

- `_start_new_hand`:桌规模式**不再内联注入**,改挂 `_blinds_pending=True` + 重置 `_active_set`;
  OCR 路径仍内联(community 未发时盲注 ROI 准,不受此 bug 影响)。
- `_tick`:pot 之后、**`_process_seat_actions` 之前**(顺序关键,否则真实动作先于合成 POST 入库 seq 乱)
  调 `_tick_active_set_and_blinds`(桌规 + has_active_hand)。
- `_tick_active_set_and_blinds`(新):每 tick `_detect_active_set(emit=False)` →
  ① **on-change emit `active_set.changed`**(成员变才记:入场/弃牌/手末 + 街)= 天然 fold 转移记录,
     ~5-10 条/手不刷库(喂 P1);
  ② **惰性注入**:活跃集非空(发牌完成)→ 派盲注/ante + 清 pending;空则等;≥12 tick 超时放弃。
- `_detect_active_set` 加 `emit` 参(per-tick 走 on-change,不每 tick `active_set.detected`)。
- `_inject_forced_from_blinds` 加 `active` 参(per-tick 传入已读,免重复扫)。
- `_TICK_PHASES` 加 `active_set`(stats 数组同步)。

## 3. 验证

- Linux:`py_compile` OK。逻辑自检:无双读、注入排在 seat_actions 前、player_id/stack 在 T+1 已就绪、
  一手一次注入、超时不无限挂。
- ⚠️ cv2/live Win-only。**验收**(重跑满桌):(a) `active=[]`/null 漏派手 → 清零(盲注派发率↑);
  (b) 新诊断 `active_set.changed` 出现,且**翻后某活跃座弃牌时 left=[该座]**(= P1 要的 fold 转移数据,
  和 fold_area OCR 读到的弃牌对齐 → 为 P2 铺路);(c) tick 多一个 `active_set` phase ~<1ms。

## 4. 计划修正记录(深读后)

- 原计划"P0 一刀解锁 P1"**不成立**(读到注入即停≠整手)→ 已改 standing per-tick。
- **P2 比预想多**:活跃集替 fold 不止 skip 逻辑,还要**合成 FOLD action_event**(带街/座)+ 标
  `_folded_seats`。本轮不做,待 P1 数据闸绿。
- 其余(P3 删死代码 / P4 升默认 / P5 拆 _process_seat_actions)不变,各自数据闸。

## 5. 严限

桌规模式专属(`_table_blinds` 在才跑);OCR 路径零变化。超时 12 tick(~12s)是经验值,看验收调。
