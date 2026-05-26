# Player_X 列表 index bug 修 + action OCR 诊断日志

- **完成时间**：2026-05-25 18:40
- **关联前次 change-log**：`change-logs/2026-05-25_18-30-00_pot_peak_bug修_amount独立ROI_第7个seat元素.md`
- **关联讨论模式**:用户实战观察 100 actions / 0 raises,确认 seat_1/seat_7 真有加注 → 不是统计性而是 bug;同步发现 Player_0/Player_1 标号错位
- **触发红线**:**R-7（pipeline 逻辑一致性 — seat_index vs 列表 index)**;**R-8(不涉及模型/识别栈本身,只调用)**
- **无关红线已检查**：R-1 到 R-6, R-9, R-10

## 1. 任务概述

讨论模式 confirmed 走 A+A 两步法:
- ①修 Player_X 列表 index bug:`enumerate i` → `seat_roi.seat_index`,让 tracker 状态 / event player_name 与物理座位对齐
- ②加 action OCR 诊断日志:每次 state-changed OCR 文本 INFO 级 log,作为 raise 识别问题的诊断输入

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | tracker 内 `_position_map` / `_prev_action_texts` / `player_id_map` 都是按 seat_index 索引 | `_capture_player_ids` 用 `seat.seat_index` 写入;`compute_positions` 返回 keyed-by-index dict |
| 2 | INFO 级 log 不会洪水 console — `check_action_change` 已 dedupe,只在文字变化时 fire | tracker.check_action_change 实现 |
| 3 | 当前 stage B 观察的 raise 缺失**不是统计性**,而是 OCR/parser 数据流问题 | 用户实战观察明确 seat_1/seat_7 有加注;100 actions 0 raises 概率极低 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `pipeline/orchestrator.py` | 修改(+14/-8 行)| `_process_seat_actions` 改为 `for seat_roi in rois.seat_regions`(不 enumerate);引入 `sidx = seat_roi.seat_index` 局部变量,贯穿全函数;`check_action_change(sidx, ...)` / `get_position(sidx)` / `_build_facing_action(sidx)` / `player_id_map.get(sidx, ...)` 全部使用 sidx;新加 INFO log: `[OCR seat_{sidx}] text={action_text!r} -> {parsed_label}`,涵盖 parse 失败场景(label='UNPARSED'),取代原 debug-level 日志 |

### 附带修复（5 分钟规则）

无(刻意忍住 — raise 识别的实际修法等用户日志数据回来再做)。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**否**
- DB schema 不变;`action_events.player_name` 字段语义不变(Text);但**写入值**会因 sidx 修正而正确反映物理座位

## 5. 红线合规动作

**R-7（pipeline 逻辑一致性)** 触发:
- [x] tracker 内部状态(player_id_map / _position_map / _prev_action_texts)均按 seat_index 索引,与本修正一致
- [x] `_capture_player_ids` 早就用 `seat.seat_index` 写入,本次让 `_process_seat_actions` 与之统一
- [x] 已有 try/except 防止 Position lookup 失败(stage A fix 已就位)

**R-8(识别栈不变)**:本次未触 recognition/* 任何模块。

## 6. 测试结果

- **语法**:`pipeline/orchestrator.py` AST parse OK ✓
- **pytest**:14 passed / 3 skipped / 0 failed ✓
- **预期数据效应**(待 Win 实测):
  - DB 中 `Player_<N>` 将正确反映物理座位(如 `Player_1` = seat_1 = 你左下邻位)
  - 每次 action OCR 文字变化均有 INFO log,raise 真出现时一定会被记录

## 7. 手动操作提醒

⚠️ **Win 端用户**:

### A. `git pull`

(本次 pull 一并拿到 4 个 commit:`3df3195..` 含 pot peak fix + amount ROI + 本 commit 等)
```powershell
cd D:\project\pokemir
git pull
```

### B. 配齐 amount + 重新框 action(如未做)

```powershell
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_1 --element action --name party_poker_8
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_1 --element amount --name party_poker_8
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_7 --element action --name party_poker_8
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_7 --element amount --name party_poker_8
```

### C. 跑 pipeline + **专注观察 raise 时的日志**

```powershell
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker_8
```

观察期间记得**截图 / 复制日志**:
- 看到玩家加注时,**console 出现的 `[OCR seat_X] text=...` 那一行截下来**
- 这能直接看到 OCR 真实读到了什么 → 定位 raise 识别根因

### D. 后续(等 C 的诊断数据)

基于日志数据,我会决定下一步:
- 如果 OCR 真读出"加注"但 parsed=UNPARSED → parser 字符匹配 bug
- 如果 OCR 读出"下注" → WePoker 加注/下注同关键字,需 amount 单调判断
- 如果 OCR 读出纯数字 → 无关键字,需 amount + orange BG 综合判断
- 如果 OCR 完全没出现 raise 时段的日志 → action ROI 框位错过加注区域

## 8. 潜在影响范围

- **正向**:
  - DB 数据正确性(Player_X 反映物理座位)
  - 用户后续配 seat_2/3/...时不会因 index drift 错位
  - raise 识别问题有诊断路径
- **行为变化**:
  - INFO log 会多一行 per state-change action;频率不高(每 action 一次)
  - 历史 DB 数据中 Player_0/Player_1 仍是错位的(但不会再产生新错位)
- **关联待办**:
  - 用户重跑 + 收集 raise 时段日志
  - 根据日志数据修 raise 识别
  - 配 button_indicator + id(stage B 收尾)

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(承接讨论 confirmed:Q1=A 两步法 / Q2=A 合并 commit)
- 产出物:1 文件改 + 本 change-log + 1 commit 待推
- 红线状态:R-7 触发,合规动作执行;其他 N/A
- pytest:14 passed,无新增 fail
- 5-min 附带修主动忍住:raise 识别的真实修法等数据回来才做
