# roi_config 加 `--all-seats` 一键批量配置全座位

- **完成时间**：2026-05-25 19:27
- **关联前次 change-log**：`change-logs/2026-05-25_19-18-00_crossval_P1_schema_signal_capture.md`
- **触发红线**：**R-7（ROI 配置工具体验)**
- **无关红线已检查**：R-1 到 R-6, R-8, R-9, R-10

## 1. 任务概述

用户反馈"只配 seat_1 + seat_7 测试效率低,数据样本不足"。需要快速给全 8/9 座配 ROI。

现有 `--field seat_N` 增量模式需 5-9 次 invoke + 每次 6-7 个 prompt;`--field seat_N --element` 粒度模式需 ~40 次 invoke。两种都太慢。

加 `--all-seats` 批量模式:**单次 invoke 完成全表配置**,内部 loop seat_0..seat_(num_seats-1),每个 seat 顺序提示所有 SEAT_ELEMENT,merge 既有 ROI。每个 seat 配完即落盘(Ctrl+C 不丢已完成进度)。

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | profile 中 `num_seats` 已正确设置(8 / 9 等) | 当前 party_poker_8.json 的 num_seats=8 |
| 2 | 用户 ESC 行为符合预期:跳过该 prompt,**保留旧值**(不破坏现有 ROI) | 与 `--field seat_N` 行为一致 |
| 3 | 配完一个 seat 立刻落盘 = 增量进度可保护(Ctrl+C 不丢) | 每 seat loop 末尾 json.dump |
| 4 | 既有 seat_1 + seat_7 配置(action/stack/amount 等)被 merge 保留,不被覆盖 | prev_entry → 复制 → captured 仅在非 None 时覆盖 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `tools/roi_config.py` | 修改(+72/-0 行)| argparse 加 `--all-seats` flag(store_true);新增分支处理(在 --field 之前):读 profile,loop range(num_seats),每个 seat 走全 SEAT_ELEMENT_ORDER 提示;merge 既有 entry;每 seat 落盘 + 状态 print(✓ ready 或 ⚠ missing required) |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**否**
- ROI JSON schema 不变;`--all-seats` 是 UX-only,产出格式与 `--field seat_N` 完全一致

## 5. 红线合规动作

**R-7（ROI 配置工具体验)** 触发:
- [x] 保留既有 ROI(merge 语义);ESC = 保留旧值
- [x] 每 seat 落盘 = 进度增量保护
- [x] 与现 `--field seat_N` 行为一致(REQUIRED_SEAT_ELEMENTS / status 提示)
- [x] 兼容部分配置:`seat 缺 action/stack` 显示 ⚠ missing,但不阻断后续 seat
- [x] num_seats=0 / profile 不存在 → 友好报错

## 6. 测试结果

- **语法**:`tools/roi_config.py` AST parse OK ✓
- **`--help` 验证**:`--all-seats` 选项出现 ✓
- **pytest**:14 passed / 3 skipped / 0 failed ✓
- **未直接 unit-test 的**:用户实战 Win 端 OpenCV 框选 — 留 Win 端测

## 7. 手动操作提醒

⚠️ **Win 端用户**:

### A. `git pull`

```powershell
cd D:\project\pokemir
git pull
```

### B. 一键配全 8 座

```powershell
.\.venv\Scripts\python.exe tools\roi_config.py --all-seats --name party_poker_8 --window "WePoker"
```

工具会:
1. 自动找窗口,截参考图
2. 从 seat_0 开始,**逐个 seat 提示 7 个 sub-ROI**(action / amount / fold_area / stack / button / cards / id)
3. 你**SPACE 确认**或**ESC 跳过(保留旧值)**
4. 每个 seat 完成后**立刻落盘**(Ctrl+C 不丢进度)

8 个 seat × 7 prompts = 56 个框选,但**一条命令完成**。

### C. 框选时的位置参考(8 座 hero-centric clockwise)

| seat | 屏幕位置 |
|:---:|:---|
| 0 | hero(底部正中,空座 + 号位置,**仅框,实际不会触发**)|
| 1 | 左下邻位 |
| 2 | 左侧中部 |
| 3 | 左上 |
| 4 | **顶部正中**(8 座独有)|
| 5 | 右上 |
| 6 | 右侧中部 |
| 7 | 右下邻位 |

每 seat 7 个元素的位置参考(终端 console 实时显示 `▶ NOW FRAMING: seat_X → ELEM` 提示):
- **action**:头像上方,显示"跟注/加注/..."(空闲时显示昵称)
- **amount**:头像旁边,chip 图标 + 数字
- **fold_area**:头像正中(弃牌时"弃牌"+ 变灰)
- **stack**:头像下方筹码总量
- **button_indicator**:筹码量左侧紧贴的小 D 标
- **cards**:对手底牌区(基本 ESC)
- **id**:与 action 同位置(框跟 action 一样)

### D. verify 看结果

```powershell
.\.venv\Scripts\python.exe tools\roi_config.py --verify --name party_poker_8
```

应看到 8 个 seat 全部色框(橙=action/stack/cards/id,黄=amount,红=fold_area,紫红=button)。

### E. 跑 pipeline

```powershell
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker_8
```

action_events 表会开始记录所有 8 座位的事件;raw_data JSONB 含 P1 全部 8 键证据。

## 8. 潜在影响范围

- **正向**:
  - 全座位配置成本从 5-9 命令降到 **1 命令**
  - 进度增量保护(Ctrl+C 不丢)
  - 仍兼容 `--field seat_N` / `--element X` 渐进模式
- **行为变化**:无(批量模式是新增,不动现 paths)
- **关联待办**:
  - 用户 Win 端跑批量配 → 数据规模扩大 → P2/P3/P4 实施时样本充分

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(用户明示提效需求)
- 产出物:1 文件改 + 本 change-log + 1 commit 待推
- 红线状态:R-7 触发,合规;其他 N/A
- pytest:14 passed,无新增 fail
