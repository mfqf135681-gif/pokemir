# 按钮权威切手搬进 live(step 2b;治公共牌 reset 过切)

## 1. 概述

观战模式换手改走**按钮 D 移座权威**(用户明示:观战=D 唯一信号 + 总底池兜底)。
治 step 2a 暴露的过切:实测 `btn=5` 连续两手(隔 9s)= 一手真牌被【公共牌 reset】
切手法劈成两半。按钮一修好(2a 白占比),钉死不动的 btn 就是铁证。

## 2. 改动

### pipeline/reconstruct.py — `button_move_online`(在线流式去抖)
- `button_moves_monotonic`(batch 回看持有时长)的 live 版:看不到未来,改【连续 debounce
  帧读到同一顺时针新座才提交移动】。同两道误读过滤(顺时针单调 (new-cur)%n∈[1,max_skip] +
  去抖)。纯逻辑,**Linux 单测过**(顺时针持稳切/单帧不切/倒退拒/None 不动)。

### pipeline/orchestrator.py
- `_scan_button_white_frac()`:抽出白占比 argmax 扫描(per-hand 定位 + per-tick 切手共用);
  `_detect_button_position` 改调它(行为不变)。
- 主循环 hand-detect:`BUTTON_CUT` 开时,每 tick 扫按钮 → `button_move_online` → 确认移座 = 换手。
- `BUTTON_CUT` 开时**关闭** hero 换牌触发 + 公共牌 reset 触发(后者=过切元凶);
  **"总底池"标签**(`_process_pot`,结算完毕信号)**仍作兜底**,不动。
- 首手仍由 `_hero_cards_present` 起(两模式都需起点);跨手按钮状态不在 `_start_new_hand` 重置。

### config.py — `BUTTON_CUT` 开关(POKEMIR_BUTTON_CUT,默认关)
default-off 闸门(沿用 DIGIT_RECIPE_LIVE 等模式),用户 A/B 验证。

## 3. 验证

- Linux:`button_move_online` 单测过(reconstruct 16 ✅);orchestrator/config `py_compile` OK;
  无循环 import(reconstruct 不反向 import orchestrator);button=None 下游守卫已确认。
- ⚠️ **白占比 cv2 + 整链路 live 行为 Win-only 未验**(盲点)。**验收标准**:
  POKEMIR_BUTTON_CUT=1 重跑,(a) 日志 `[step2b] 按钮移座 → seat N,换手` 随真按钮移动出现;
  (b) DB 里【相邻两手 button_seat_index 不再相同】(过切消失,btn 单调递进);
  (c) 平均动作/手 上升(碎片合并)。

## 4. 严限 / 回滚

- debounce=2(约 2 tick≈1-2s):换手略滞后,可能漏新手前 1-2 个动作(可调小到 1 看验收)。
- 仅观战模式设计;hero-playing 模式未覆盖(BUTTON_CUT 开会关掉 hero 换牌触发)。
- 回滚=POKEMIR_BUTTON_CUT 不设(默认关)→ 完全回到旧公共牌 reset 切手。
- 关联 #224(回放并集切手)、T139(按钮权威,回放);本次是其 live 落地。
