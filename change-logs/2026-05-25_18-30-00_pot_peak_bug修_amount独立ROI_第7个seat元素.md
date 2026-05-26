# Pot peak bug 修 + amount 独立 ROI 第 7 个 seat 元素

- **完成时间**：2026-05-25 18:30
- **关联前次 change-log**：`change-logs/2026-05-25_18-10-00_fix_stageB_pipeline_crash_num_seats_position_fk.md`
- **关联讨论模式**:用户实战观察到 (1) pot_size_final=7 但实际 2235 (2) action 与 amount WePoker 中分离;讨论模式确认走方案 A
- **触发红线**：**R-7（ROI 配置一致性 — schema 加 amount_area)**
- **无关红线已检查**：R-1 到 R-6, R-8, R-9, R-10

## 1. 任务概述

用户实战观察 stage B 后两个发现:

1. **`hands.pot_size_final = 7`** 实际真值是 2235(日志显示):`finalize_hand` 用了 `latest_pot_bb` 这个变量,但在 community 重置(新 hand 开始)瞬间,pot OCR 可能读到过渡值污染 last
2. **WePoker action 与 amount 物理分离**:action 区(头像上方)只显示"跟注/加注/...",amount 在头像旁边(chip icon + 数字);现 schema 只有 action ROI 无法捕获金额

修两件:
- **A1**: `pot_size_final` 用本手 pot 峰值(_hand_pot_peak)而非 last
- **A2**: SeatROI 加第 7 个元素 `amount_area`,orchestrator 把 amount OCR 与 action 拼接送 parser

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | pot 在 hand 内**只增不减**(扑克游戏规则),峰值 = 终值 | 扑克 fundamentals |
| 2 | amount 区 OCR 用 digit allowlist 即可过滤掉 chip 图标,无需先做图像 mask | 用户实战确认 amount 显示格式 = Q2A(纯数字 + 图标);EasyOCR allowlist 是 character whitelist,非 ASCII / 非数字字符会被丢弃 |
| 3 | parser `\d+\.?\d*` regex 从 `"跟注 100"` 抽出 100,无需新 parser 逻辑 | recognition/actions.py 既有 AMOUNT_RE |
| 4 | 旧 profile(无 amount_area)pipeline 加载仍工作:`capture/roi.py::from_dict` `s.get("amount")` 守护 | additive schema 设计 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `pipeline/detector.py` | 修改(+5/-2 行)| `StateTracker.__init__` 加 `_hand_pot_peak` 字段;`start_new_hand` 重置;`finalize_hand` 用 peak 写 pot_size_final;docstring 解释 transient 污染 |
| `pipeline/orchestrator.py` | 修改(+8/-2 行)| `_process_pot` 同步更新 `_hand_pot_peak = max(prev, amount)`;`_process_seat_actions` 加 amount OCR 分支(`seat_roi.amount_area` 存在时,OCR + digit allowlist + 拼接到 action_text) |
| `capture/roi.py` | 修改(+10/-2 行)| `SeatROI` 加 `amount_area: ROIRegion \| None`;`to_dict` 输出 amount;`from_dict` 兼容 missing key |
| `tools/roi_config.py` | 修改(+4/-2 行)| `SEAT_ELEMENT_ORDER` 加 amount(放第 2 位);`ELEMENT_HINTS["amount"]` 文案;`action` 文案重写强调"只动作汉字,无金额";`_draw_rois` 加 amount 框(黄色,与 pot 同色,语义"金额数字");full-setup 保存路径加 amount 进 opt 列表 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**否**
- ROI JSON schema 加 `amount` 字段是 additive;现有 party_poker_8.json / party_poker_9.json 无 amount 字段时,pipeline 不报错,只是 amount 一直 None(回退到现行为)

## 5. 红线合规动作

**R-7（ROI 配置一致性)** 触发:
- [x] amount_area 是 additive ROI,旧 profile 兼容
- [x] orchestrator 行为:有 amount_area 才 OCR + 拼接;无则维持现状(action_text 不含金额)
- [x] verify 视图加黄色框区分 amount 与其他;红色 fold / 橙色 stack / 紫红 button

## 6. 测试结果

- **pytest**:14 passed / 3 skipped / 0 failed ✓
- **`--help` 验证**:`--element {action,amount,fold_area,stack,button_indicator,cards,id}` ✓
- **rules-dev §5.2 判定**:✓ 通过

未直接测的:
- pot peak 在真实多 tick 场景行为(需 Win 端实测,本次留给用户回归)

## 7. 手动操作提醒

⚠️ **Win 端用户**:

### A. `git pull`

```powershell
cd D:\project\pokemir
git pull
```

### B. 重新框 action(更紧)+ 加配 amount

WePoker 实测:**action 区只含汉字** + **amount 在旁边独立显示**。

```powershell
# 重新框 seat_1 action(只圈"跟注"两字范围,不包含金额)
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_1 --element action --name party_poker_8

# 新配 seat_1 amount(头像旁边的金额数字区,可含 chip 图标)
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_1 --element amount --name party_poker_8

# 同样对 seat_7
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_7 --element action --name party_poker_8
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_7 --element amount --name party_poker_8
```

每次执行 console 会看到 `▶ NOW FRAMING:  seat_1 → AMOUNT` 提示 + 位置说明。

### C. 等机会配 button_indicator + id(可选)

- button:D 按钮真轮到 seat_1 或 seat_7 时框
- id:hand-start 1-2 秒内,昵称显示时框跟 action 一模一样的区域

### D. 跑 pipeline 验证

```powershell
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker_8
```

预期:
- ✓ `Pot peak` 在 hand_end 日志中应是本手最大值(不再是 7 这种小数)
- ✓ action_events 表里 call/bet/raise 的 amount 字段开始有具体数字

### E. raise 识别(待你回归后回报)

之前讨论用户观察 raise 没出现,有 4 个原因可能(详见上次讨论)。**做完本次 ROI 重配后再观察几手 raise**,届时回报观察结果:
- 如果 raise 仍未出现 + 玩家确实加注了 + 真在 seat_1/seat_7 → 我加 parser fallback
- 如果 raise 出现 → 不修

## 8. 潜在影响范围

- **正向**:
  - pot_size_final 数据正确性恢复
  - action_events.amount 字段开始填值(stage B 数据完整性提升)
  - amount 与 action 物理解耦,符合 WePoker 真实 UI
- **行为变化**:
  - amount ROI 未配的 seat:行为同此前(amount=None)
  - amount ROI 已配的 seat:OCR 多一次调用,~10-30ms × N seat,500ms tick 仍宽裕
- **关联待办**:
  - 用户重配 action + 加配 amount
  - 用户观察 raise 是否出现
  - button_indicator + id 待配(stage B 完整收尾)

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(承接讨论模式 confirmed:Q1=A 重框 action + 加 amount,Q2=A 路径走 pot fix + amount ROI)
- 产出物:4 文件改 + 本 change-log + 1 commit 待推
- 红线状态:R-7 触发,合规动作执行;其他 N/A
- pytest:14 passed,无新增 fail
- 5-min 附带修主动忍住:raise parser fallback 等用户回归确认才做
