# Profile 分立 8/9 座: party_poker.json → party_poker_9.json + 准备 party_poker_8.json

- **完成时间**：2026-05-25 17:28
- **关联需求讨论**：`requirement-discussions/2026-05-25_02-08-00_PathA第4步阶段B_seats×30_ROI配置.md`(confirmed,持续实施)
- **关联记忆**：[[wepoker-table-layout-8v9]] · [[path-a-step-4-stage-a-accepted]]
- **关联前次 change-log**:`change-logs/2026-05-25_03-14-00_stageB三次迭代_hero_centric_seat_button_ocr_resource_gitignore.md`
- **触发红线**：**R-7（ROI 配置一致性 — profile 文件命名约定）**
- **无关红线已检查**：R-1 到 R-6, R-8, R-9, R-10

## 1. 任务概述

用户讨论模式提出需求:观战时桌型(8/9 座)不可控,需要随时切换 profile。

按 [[wepoker-table-layout-8v9]] 既定结论,8 座与 9 座**顶部几何不重合**,无法单 profile 自适应 → 必须双 profile。

最简方案 α(用户讨论 Q1 confirmed):
- 文件分立 `rois/party_poker_8.json` + `rois/party_poker_9.json`
- 切换 = Ctrl+C 停 pipeline + 上箭头改 `--profile` + 重跑 (~5 秒)
- 基建 `main.py --profile NAME` 早已就位,本次仅做命名规范化

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 现 `rois/party_poker.json` 内容是 9 座 schema(num_seats=9) | 上轮 161cd35 commit 已改 |
| 2 | 用户切桌之间会停 pipeline,Ctrl+C 重启 5 秒成本可接受 | 用户讨论 Q1=A confirmed |
| 3 | 命名 `party_poker_9` 比 `wepoker_9` 更稳(party_poker 是 placeholder,已贯穿全代码;改平台前缀需多处同改,scope 蔓延) | 用户讨论 Q2=A 选择 |
| 4 | record_card.py 的 DEFAULT_PROFILE 改名后,既有 fixture 录制流程不破坏(record_card 只是用 profile 找 ROI 校准,profile 文件名变即跟着变) | 读 tools/record_card.py 现实 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `rois/party_poker.json` → `rois/party_poker_9.json` | 重命名 + 内容微调 | `git mv` 重命名;文件内 `"name": "party_poker"` → `"party_poker_9"`(自描述属性 sync) |
| `config.py` | 修改(+1/-1)| `ROI_PROFILE` env 默认值 `party_poker` → `party_poker_9` |
| `main.py` | 修改(+1/-1)| `--profile` help 文案改示例为 `party_poker_9, party_poker_8` |
| `tools/record_card.py` | 修改(+1/-1)| `DEFAULT_PROFILE = "party_poker_9"` |
| `tools/roi_config.py` | 修改(+1/-1)| docstring 示例 `--name party_poker_9` |

### 附带修复（5 分钟规则）

无(用户后续配 8 座 profile 时跑 `roi_config.py --name party_poker_8`,本次不预生成空文件,避免歧义)。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**否**
- ROI profile JSON schema 无变化(仅文件名 + 自描述字段)

## 5. 红线合规动作

**R-7（ROI 配置一致性)** 触发:
- [x] 文件重命名用 `git mv`,git 历史保留 rename detection
- [x] 所有 hardcoded "party_poker" 引用同步更新(grep 全集已处理:5 处)
- [x] env var 兼容:用户若设 `POKEMIR_ROI_PROFILE=xxx` 不受默认值改动影响
- [x] 不预生成空 `party_poker_8.json`,避免 pipeline 启动时加载空文件 crash

## 6. 测试结果

- **重命名后 rois/ 目录**:
  ```
  rois/party_poker_9.json   ← 现有 9 座 profile
  rois/.gitkeep             ← 占位(如有)
  ```
- **grep 验证**:`party_poker` 无误剩(仅在 change-log/REQ 历史文档中作为旧名称引用,符合 R-9 文档不溯改)
- **pytest**:14 passed / 3 skipped / 0 failed ✓
- **rules-dev §5.2 判定**:✓ 通过

## 7. 手动操作提醒

⚠️ **Win 端用户接下来的操作**:

### A. `git pull` 拉本次 commit

```powershell
cd D:\project\pokemir
git pull
```

会 Apply 一个 rename:`rois/party_poker.json` → `rois/party_poker_9.json`(git 自动处理)

### B. 跑 pipeline(默认 9 座 profile)

```powershell
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker_9
```

或者完全不带 `--profile`(用 env 默认值 party_poker_9):
```powershell
.\.venv\Scripts\python.exe main.py pipeline
```

### C. 遇到 8 座桌时,先配 ROI(独立流程)

```powershell
# 第一次配:roi_config 从零开始建 party_poker_8.json
.\.venv\Scripts\python.exe tools\roi_config.py --name party_poker_8 --seats 6

# 注意 --seats 实际指的是"几个 seat",8 座 H5 桌可能用 --seats 6 也可能其他值
# 因为 8 人桌的 hero + 7 others + 总共 8 个,但 _get_seat_labels 当前只实现了 6/9 的明确 layout
# → 8 座专属 layout 设计留 sub-REQ;本次先支持 profile 切换基建,具体 8 座配置等真有桌出现时讨论
```

⚠️ **8 座 profile 的 seat 命名/几何 layout 留独立 sub-REQ**(本次不预设)。

### D. 切桌实战

```powershell
# 在 9 座桌跑
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker_9
# Ctrl+C 停

# 切 8 座桌
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker_8
```

## 8. 潜在影响范围

- **正向**:
  - 8/9 座桌自由切换基建到位
  - 未来加平台/桌型只是 `<name>.json` 文件平行扩展,代码零改
- **行为变化**:
  - 用户原跑 `--profile party_poker` 会报"不存在",需改 `--profile party_poker_9`(默认值已同步,可省略 --profile)
  - Win 端 git pull 后会看到 rename 操作,不会丢数据
- **关联待办**:
  - 8 座桌实际几何 layout(_get_seat_labels(8) / 顶部单人位置)— 等真有 8 座桌实战后开 sub-REQ
  - 用户 stage B B2 实际配置 seat_1 / seat_8(本次 push 不阻塞,可继续)

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(承接讨论 confirmed:Q1=α / Q2=A)
- 产出物:5 文件改(含 1 rename)+ 本 change-log + 1 commit 待推
- 红线状态:R-7 触发,合规动作执行;其他 N/A
- pytest:14 passed,无新增 fail
- 5-min 附带修主动忍住:8 座 `_get_seat_labels(8)` 未预设;`party_poker_8.json` 未预生成
