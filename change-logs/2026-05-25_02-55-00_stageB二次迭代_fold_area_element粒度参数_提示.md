# Stage B 二次迭代: fold_area 独立 ROI + `--element` 粒度参数 + 框选提示

- **完成时间**：2026-05-25 02:55
- **关联需求讨论**：`requirement-discussions/2026-05-25_02-08-00_PathA第4步阶段B_seats×30_ROI配置.md`(confirmed,本次为 stage B 实施过程中的两轮讨论模式产出)
- **关联前次 change-log**:`change-logs/2026-05-25_02-39-00_9座支持_VALID_FIELDS扩至seat8_profile_num_seats9.md`
- **触发红线**：**R-7（ROI 配置一致性 — schema 加字段 + 工具语义升级)**
- **无关红线已检查**：R-1 到 R-6, R-8, R-9, R-10

## 1. 任务概述

用户讨论模式提供两条关键 UX 情报:
1. **fold 和其他动作物理位置不同**:WePoker 中 call/raise/check/bet 在头像**上方**(与 ID 像素重叠);fold 在头像**正中**(头像同步变灰)
2. **30+ ROIs 一次性框选太难**,需要"一次只配一个"的粒度

两点合并实施:
- **A.** SeatROI 加 `fold_area` 第 6 个字段;orchestrator 优先 fold 后 action
- **B.** `--element <name>` 参数:`--field seat_N --element fold_area` 只框该 1 个;不带 `--element` 仍弹 6 个顺序框
- **C.** 每次框选前 console 打印**清晰提示**:`▶ NOW FRAMING: seat_N → ELEMENT` + 位置说明

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | WePoker fold "弃牌" 文字 + 头像变灰**永远同时发生**;OCR 主信号已足够 | 用户讨论确认 |
| 2 | call/raise/check/bet 等动作文字与 ID 在同像素;ID 在 hand-start 读一次后缓存,后续 OCR action 区不会冲突 | 现 `_extract_player_ids` 实现是 hand-start 调用一次,与 `_process_seat_actions` 不同 phase |
| 3 | fold 文字"弃牌"在 `recognition/actions.py` parser 中已支持(中文支持上一轮加过)| 阶段 A change-log `2026-05-25_00-53-00` 验证 |
| 4 | 现 5-元素 schema 兼容 — fold_area 是可选(`s.get("fold_area")` 走 if 分支)| `capture/roi.py::from_dict` 现实读 |
| 5 | 增量合并(prev_entry + captured)语义:未 prompt 元素 = 保留旧值;prompt 但 ESC 元素 = 也保留旧值(不 clear)| 用户明确希望多次 invoke 累加配置;不希望"忘配某个就清掉" |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `capture/roi.py` | 修改(+13/-2 行)| `SeatROI` 加 `fold_area: ROIRegion | None`;`to_dict` 写出 fold_area 字段(如 truthy);`from_dict` 读 fold_area(如有);docstring 加 WePoker layout 说明 |
| `pipeline/orchestrator.py` | 修改(+10/-3 行)| `_process_seat_actions` 加 fold-priority:先读 `seat_roi.fold_area` OCR,若文本含"弃牌"则用作 action_text;否则常规读 action_area |
| `tools/roi_config.py` | 修改(+60/-30 行)| 新增 `SEAT_ELEMENT_ORDER` + `REQUIRED_SEAT_ELEMENTS` + `ELEMENT_HINTS` 常量;新增 `--element` argparse 参数(6 choice);重写 seat_N 分支:支持单元素 / 全 6 元素模式,合并 prev_entry + captured,ESC 保留旧值,最终保存校验 action+stack;主交互路径(full setup)同步加 fold_area 在 6 元素序列内;`_draw_rois` 加红色 fold_area 框 |

### 附带修复（5 分钟规则）

无(主动忍住继续扩大 scope;`_get_seat_labels(9)[8]="center-left"` 标签奇怪问题留待真用 seat_8 时再深究)。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**否**
- ROI profile JSON schema 加 `fold_area` 是 additive change;旧 profile(无 `fold_area` 字段)仍可加载

## 5. 红线合规动作

**R-7（ROI 配置一致性)** 触发:
- [x] schema 加字段是 additive — 旧 profile 加载仍工作(`from_dict` `s.get("fold_area")` 防御)
- [x] orchestrator 行为变化(fold 优先)— fold_area 为 None 时 fallback 原 action 流程,完全兼容现有未配 fold_area 的 seat
- [x] 半配防护升级:最终保存前校验 `action` + `stack` 必填,允许中间状态分多次配
- [x] verify 模式同步:`_draw_rois` 加红色 fold_area 框 — 用户能眼看 fold_area 位置

## 6. 测试结果

- **语法**:`tools/roi_config.py` / `capture/roi.py` / `pipeline/orchestrator.py` AST parse OK ✓
- **`--help` 验证**:`--element` 选项含 6 个 choice:`{action,fold_area,stack,button_indicator,cards,id}` ✓
- **常量自洽**:`SEAT_ELEMENT_ORDER` 与 `ELEMENT_HINTS.keys()` 集合相等 ✓
- **profile load 兼容**:`rois/party_poker.json`(`seats: []`)用新 parser 加载无 error ✓
- **pytest**:14 passed / 3 skipped / 0 failed(与上版本一致)✓

## 7. 手动操作提醒

⚠️ **Win 端用户(stage B 配 ROI 新流程)**:

### A. `git pull`

```powershell
cd D:\project\pokemir
git pull
```

### B. 推荐配置流程(2 seats × 6 ROIs = 12 个 ROI,可分 12 次 invoke)

**先抓不依赖游戏画面的 4 个**(随时都能配,不用等画面):
```powershell
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_4 --element stack --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_4 --element button_indicator --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_6 --element stack --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_6 --element button_indicator --name party_poker
```

**等到画面合适时再抓 action / fold_area**:
- 看到玩家动作文字(跟注/加注/...) → 立刻配 `--element action`
- 看到玩家弃牌(头像变灰 + "弃牌"字) → 立刻配 `--element fold_area`

```powershell
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_4 --element action --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_4 --element fold_area --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_6 --element action --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_6 --element fold_area --name party_poker
```

**id / cards 可跳**(--element id 直接框跟 action 一模一样的区域;cards 极少用 ESC 即可):
```powershell
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_4 --element id --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_6 --element id --name party_poker
```

### C. 每次执行,你会看到这样的 console 提示

```
============================================================
  SEAT 4: framing 1 element — un-prompted elements keep existing values
============================================================

▶ NOW FRAMING:  seat_4 → FOLD_AREA
  位置说明:    头像正中,玩家弃牌时显示「弃牌」两字 + 头像变灰(独立于上方动作区)
  操作:        鼠标拖框 → 按 SPACE 确认 / 按 ESC 跳过(保留旧值)
```

### D. verify(可随时看进度)

```powershell
.\.venv\Scripts\python.exe tools\roi_config.py --verify --name party_poker
```

颜色编码:
- 橙色 = seat 5 元素中的 action/stack/cards/id
- **红色 = fold_area**(新加;特别区分)
- 紫红 = button_indicator
- 蓝 = community
- 黄 = pot
- 绿 = hero

### E. 跑 pipeline 落数据

```powershell
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker
```

挂 10+ 手,贴日志给我;我远端查 action_events 表。

## 8. 潜在影响范围

- **正向**:
  - 弃牌精准识别(此前完全无法捕获)
  - 用户体力大幅降低(不必等所有 6 个画面同时就位)
  - verify 视觉反馈更清晰(红色 fold_area 与橙色 action 一眼区分)
- **行为变化**:
  - 现有 schema 加 `fold_area` 是 additive,旧 profile 兼容
  - orchestrator 每个 seat 多一次 OCR(fold_area)— ~5-10ms × 9 座 ≈ 50-90ms 额外延迟,pipeline 500ms tick 仍宽裕
  - `--element` 不带值时仍是全 6 元素顺序框(向后兼容)
- **关联待办**:
  - 用户 stage B B2 用新工具配 seat_4 / seat_6
  - 联调 fold 识别准确度
  - 剩余 7 个 seats — 后续会话

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(承接两轮讨论模式 confirmed:fold_area 6-ROI 模型 + α `--element` 粒度)
- 产出物:3 文件改 + 本 change-log + 1 commit 待推
- 红线状态:R-7 触发,合规动作执行(additive schema + 兼容 fallback + 半配防护);其他 N/A
- pytest:14 passed,无新增 fail
- 5-min 附带修主动忍住:`seat_8 "center-left"` 标签待用到再修
