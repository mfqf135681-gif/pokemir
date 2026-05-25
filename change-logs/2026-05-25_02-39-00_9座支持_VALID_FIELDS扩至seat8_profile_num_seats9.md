# 9 座支持: VALID_FIELDS 扩至 seat_0..seat_8 + party_poker.json num_seats=9

- **完成时间**：2026-05-25 02:39
- **关联需求讨论**：`requirement-discussions/2026-05-25_02-08-00_PathA第4步阶段B_seats×30_ROI配置.md`(confirmed,本 commit 是 stage B 前置扩展)
- **关联记忆**：[[wepoker-table-layout-8v9]] · [[path-a-step-4-stage-a-accepted]]
- **关联前次 change-log**:`change-logs/2026-05-25_02-16-00_stageB准备_B1_seatN工具_B4_pot持久化.md`
- **触发红线**：**R-7（ROI 配置一致性 — num_seats 改变会影响 compute_positions 模运算)**
- **无关红线已检查**：R-1 到 R-6, R-8, R-9, R-10

## 1. 任务概述

用户在讨论模式确认当前 WePoker 桌为 **9 人桌**(`[[wepoker-table-layout-8v9]]` 记录)。原 stage B 工具 + base profile 都是 6 座 schema:
- `tools/roi_config.py` 的 `VALID_FIELDS` 只含 `seat_0..seat_5`
- `rois/party_poker.json` 的 `num_seats: 6`

如继续用 6 座 schema 配:
- 用户在 9 座桌上只能配前 6 个 seat 位置(seat_6..8 不存在 choice)
- 即使配齐 6 个,`capture/roi.py::compute_positions` 用 `num_seats=6` 做模运算,9 座桌的 button-relative position 会全部错位

→ 路径 A:**先扩到 9 座再配 ROI,position 标签从开始就正确**(对比路径 B 强配 6 座 schema 后续推倒重来)

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | WePoker 9 座桌的 seat_index 标签符合 `_get_seat_labels(9)` 的 layout(0=top-left, 1=top, 2=top-right, 3=right, 4=bottom-right, 5=bottom/hero, 6=bottom-left, 7=left, 8=center-left)| `tools/roi_config.py:43` 既定约定 |
| 2 | `_get_seat_labels(9)[8] = "center-left"` 这个 layout 标签虽奇怪,但**当前 stage B 验证只用 seat_4 / seat_6,不触发 seat_8**,后续若用到再深究 | code 实读 + scope 边界 |
| 3 | 加 num_seats=9 不影响 community_cards / pot_size / hero_card_* 的 ROI(它们独立于 seats)| `capture/roi.py::from_dict` 字段分离;community 解析无 seat 依赖 |
| 4 | 现有 5 个 community ROI 像素坐标在 9 座桌也成立(community 区在桌面正中,与人数无关)| WePoker UI 通用约定 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `tools/roi_config.py` | 修改(+1/-1 行)| `VALID_FIELDS` 中 `range(6)` → `range(9)`;`--field` choices 现含 seat_0..seat_8 共 9 个 |
| `rois/party_poker.json` | 修改(+1/-1 行)| `num_seats: 6` → `9`;影响 `compute_positions` 模运算正确性 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**否**
- ROI 文件 schema 不变(num_seats 是数值字段,从 6 改 9 是值变化非 schema 变化)

## 5. 红线合规动作

**R-7（ROI 配置一致性)** 触发:
- [x] num_seats 改变影响 `compute_positions` 行为 — 验证 `capture/roi.py:150 relative = (i - button_seat_index) % num_seats` 模 9 正确
- [x] 现有 seats: [] 不破坏(等用户重配)
- [x] community / pot ROI 保留(本次仅改 num_seats + 工具 choices)
- [x] 工具扩展不破坏既有 seat_0..5 行为(只是额外允许 seat_6..8)

## 6. 测试结果

- **`--help` 验证**:
  ```
  --field {community_1,...,seat_0,seat_1,...,seat_8}
  ```
  共 17 个 choice,seat_8 等新增项可用 ✓

- **pytest 全套**:14 passed / 3 skipped / 0 failed(同 stage B 准备版本一致) ✓

- **profile load smoke**(隐式):后续 pipeline 启动时 `from_dict` 读 num_seats=9 后,`compute_positions` 用模 9 算 — pre-conditions 成立(stage B 用户 git pull 后才会真测)

## 7. 手动操作提醒

⚠️ **Win 端用户接下来的操作**:

1. `git pull` 拉本次 commit

2. **配 seat_4 + seat_6**(hero 紧邻左右,字大最清楚):
   ```powershell
   .\.venv\Scripts\python.exe tools\roi_config.py --field seat_4 --name party_poker
   .\.venv\Scripts\python.exe tools\roi_config.py --field seat_6 --name party_poker
   ```

   - **seat_4** = bottom-right(屏幕右下,**自己右侧邻居**)
   - **seat_6** = bottom-left(屏幕左下,**自己左侧邻居**)

3. verify:
   ```powershell
   .\.venv\Scripts\python.exe tools\roi_config.py --verify --name party_poker
   ```
   橙色框框应叠在 seat_4 / seat_6 玩家头像下方的"动作文字 + 筹码"区。

4. 跑 pipeline 观战 5+ 手 → 日志贴我,我远端查 action_events。

## 8. 潜在影响范围

- **正向**:
  - 9 座 button-relative position 标签从 stage B 第一手起就正确(VPIP/PFR 未来统计无重做风险)
  - 工具支持上限到 9 座(WePoker 主流桌型范围)
- **行为变化**:
  - 若 future 切到 8 座桌,需另一个 profile(`[[wepoker-table-layout-8v9]]`)— 不在本次 scope
  - `_get_seat_labels(9)[8] = "center-left"` 这个奇怪 layout 标签留待后续若真用到 seat_8 再深究
- **关联待办**:
  - stage B 用户侧(B2 + B3) — 等用户做
  - 8 座专属 profile — 未来切桌型时启动

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(承接 REQ confirmed `2026-05-25_02-08-00`,用户讨论后路径 A 选择)
- 产出物:2 文件改(代码 1 行 + JSON 1 行) + 本 change-log + 1 commit 待推
- 红线状态:R-7 触发,合规动作执行;其他 N/A
- pytest:14 passed,无新增 fail
- 5-min 附带修主动忍住:profile 既有 community ROI 坐标 / hero ROI null 现状不动
