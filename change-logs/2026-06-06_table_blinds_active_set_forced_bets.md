# 桌规录入 + 精确活跃集确定性派强制注(治 #230 盲注抖动)

## 1. 概述

用户提案:脚本启动录入 SB/BB/ante,然后**按按钮 + 精确活跃集**确定性派强制注,绕开 OCR 盲注
读取(#230 抖动源)。关键约束(用户纠正):**必须用 card_marker 精确活跃集,不能用占座**
——占座≠发牌(筹码带入审核 / 坐下未发牌等);活跃集检测是二元 phash 对比,8 次/手,极便宜。

## 2. 改动

### pipeline/reconstruct.py — `blinds_from_button`(纯逻辑,Linux 单测)
从按钮在**活跃集**里顺时针取 SB=下一活跃座 / BB=下下一活跃座(**跳空座,非盲目 D+1/D+2**);
单挑(活跃=2)按钮=SB、对家=BB;活跃<2 或无按钮 → (None,None)。单测:满座/跳空座/环绕/单挑/不足/无按钮。

### capture/roi.py — SeatROI 加 `card_marker` + `card_marker_ref` 字段 + loader
profile JSON 早有这俩(8 座 ref 已采),但 dataclass 没加载 → 补上。

### pipeline/orchestrator.py
- `_read_table_blinds`:env(POKEMIR_SB/BB/ANTE)优先,否则 tty 交互 prompt;非交互(replay/cron)
  返 None 不阻塞 → 回落 OCR。__init__ 存 `self._table_blinds`。
- `_avg_hash_int`(**忠实复制 replay_reconstruct._avg_hash**,BGRA→GRAY 适配 mss;必须同算法才能跟
  profile 的 int ref 比)+ `_hamming64` + `_detect_active_set(th=8)`(card_marker phash 对 ref hamming≤8=在手)。
- `_inject_forced_from_blinds`:按钮 + 活跃集 → `blinds_from_button` 定 SB/BB 座;**ante 给全活跃座** +
  SB/BB 给算出座,用户输入值,造 POST_ANTE/POST_SB/POST_BB synthetic events;raw_data 记
  blind_level/active_set/sb_seat/bb_seat(source=table_input)。玩家未知则跳(不伪造名)。
- `_inject_post_events` 顶部分支:`_table_blinds` 在 → 走新路径;否则原 OCR 路径(零变化)。
- `_start_new_hand`:`_table_blinds` 在 → 跳 `_detect_blind_levels`(OCR),灭 #230。

## 3. 为什么用 card_marker 不用占座 / 不用 imagehash

- 占座≠发牌(用户:带入审核等)→ 必须 card_marker 精确"在手"。
- detector 的头像注册表用 `imagehash` 库;但 card_marker_ref 是 replay 自定义 `_avg_hash`(8x8)算的
  **整数**——必须同算法才可比(又一次 harness-vs-live 同源,见 [[harness-vs-live-codepath-divergence]])。

## 4. 验证

- Linux:`blinds_from_button` 单测过(reconstruct 18 ✅);全文件 `py_compile` OK;`os` 已导入、
  `_position_map`/`player_id_map`/`_prev_stack` 均在 tracker;ActionType.POST_ANTE 存在。
- ⚠️ cv2 路径(_avg_hash_int/_detect_active_set/capture)+ 整链路 Win-only 未验。
- **验收**(Win,设 POKEMIR_SB/BB[/ANTE] 重跑):(a) 日志 `[桌规派注] 活跃集{...} 按钮N → SB/BB`;
  (b) DB blind_level_source=table_input、sb_seat/bb_seat = 活跃集走位(有空座时≠button+1/+2);
  (c) POST_ANTE 覆盖全活跃座;(d) 盲注值=输入值(不再抖)。

## 5. 严限 / 回滚

- 不设独立闸门:**提供盲注 = 开启**;不提供 → 原 OCR 路径零变化(安全默认)。
- 活跃集时机:开手注入时取 card_marker;BUTTON_CUT 去抖~1-2s,极早弃牌者可能漏(SB/BB 强制注不受影响)。
- 玩家 ID 未解析的活跃座 ante/盲注会跳(不伪造名,与旧路径一致)。
- 关联 #230(盲注抖动,本次根治)、#228(活跃集接 live,本次落地 blind 用途)。
