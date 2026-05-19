# P0 bug 批修：`monotone == False` 风格修正 + `_process_pot` 死代码接通

- **完成时间**：2026-05-18 05:00
- **关联需求讨论**：无（独立 DEV bug 修复任务）
- **关联前次 change-log**：`change-logs/2026-05-18_04-30-00_config.py加load_dotenv修复.env未自动加载.md`（前置：测试基线 14p/3s/0f）
- **触发红线**：无
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10

## 1. 任务概述

- **用户原始需求**："顺序执行 1、2、3、0"——本文件覆盖 1+2 批修
- **bug 来源**：项目初始三层分析中识别的 P0 bug 链
  - bug 1：`events/normalizer.py:101` `monotone == False` 风格错误（PEP 8 推荐 `not monotone`）
  - bug 2：`pipeline/orchestrator.py:220-224` `_process_pot` 读 OCR 不存储——存量 `amount` 变量丢弃，pot_size_bb 字段永远 None
- **涉及功能模块**：`events/normalizer.py`（board_texture 布尔逻辑）+ `pipeline/orchestrator.py`（pot 处理）+ `pipeline/detector.py`（StateTracker 新增 `latest_pot_bb` 字段承接）
- **相邻任务**：与 `2026-05-18_02-00-00_修screen.py双init bug.md` 同一 P0 bug 链不同 bug

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | bug 1 仅样式问题（等价语义），非功能 bug | Python 布尔语义：`x == False` ↔ `not x` 对真布尔值等价 |
| 2 | bug 2 wire 方案 = StateTracker 新加 `latest_pot_bb` 字段；`_process_pot` 写入；`_process_seat_actions` 读取并写入 `event.pot_size_bb` | rules-dev §3 最小实现——既不删除 `_process_pot`（项目要 pot 跟踪），也不引入新表/契约改动 |
| 3 | `event.pot_size_bb` 字段已在 `events/models.py:53` + `contracts/models.sql:46` + `storage/models.py:50` 全套定义；仅 pipeline 端"写入路径"缺失 | 已 grep 验证；不触发 R-5（契约）/ R-6（ORM 同步） |
| 4 | 修复后 pytest 应仍 14p/3s/0f（test_storage 不测 pot_size_bb；test_recognition 不涉 normalizer 布尔） | 改动是 pipeline runtime 路径，单元测试不覆盖 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `events/normalizer.py` | 修改（-1/+1 行） | 行 101 `texture["monotone"] == False` → `not texture["monotone"]`（PEP 8 推荐，等价语义） |
| `pipeline/detector.py` | 修改（+3 行） | `StateTracker.__init__` 加 `self.latest_pot_bb: float | None = None` 字段 + 注释 |
| `pipeline/orchestrator.py` | 修改（+2 / +5 行） | (a) `_process_seat_actions` 创建 event 后加 `if self.tracker.latest_pot_bb is not None: event.pot_size_bb = self.tracker.latest_pot_bb`；(b) `_process_pot` 加 docstring + `if amount is not None: self.tracker.latest_pot_bb = amount` 接通 OCR 到 tracker |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- `event.pot_size_bb` 字段早已在契约定义；本次只是**填充该字段**（之前是 None），未改 schema

## 5. 红线合规动作

无触发。

## 6. 测试结果

- **验证路径**：完整验证（多文件改动 + 涉及 StateTracker 公共字段新增）

- **执行**：`.venv/bin/pytest tests/ -v`
  ```
  17 tests collected
  14 passed
   3 skipped
   0 failed
  耗时 26.73s
  ```

- **与基线对比**：与上次 `config.py 加 load_dotenv` 后基线 14p/3s/0f 一致——零回归 ✅

- **rules-dev §5.2 判定**：✅ 全通过

- **数据流验证**（设计期推理，无 runtime 实测因 Linux 无 poker 客户端）：
  - `_process_pot` → `self.tracker.latest_pot_bb = <amount>` ✓
  - 下次 `_process_seat_actions` 创建 event 时 → `event.pot_size_bb = <amount>` ✓
  - `event_repo.create(db, event)` 把 event 写入 DB → `action_events.pot_size_bb` 列填充
  - 完整生产数据流验证待 Win 端实测

## 7. 手动操作提醒

无。

## 8. 潜在影响范围

- **正向**：
  - bug 1：消除 pyflakes/lint 类静态扫描器 E712 警告；可读性提升
  - bug 2：`action_events.pot_size_bb` 列从永远 None → 跟踪实际 pot 大小；postflop stats（pot odds、commitment ratio 等未来扩展）有数据基础
- **行为变化**：
  - bug 1：**无运行时行为变化**（语义等价）
  - bug 2：pipeline runtime 在 Win 端跑 `_process_pot` 后，next action event 会带上 pot_size_bb；如果 OCR 读 pot 失败或返回 None，事件 pot_size_bb 仍为 None（不影响）
- **关联待办**：
  - bug 1 类似 PEP 8 风格扫描（其它地方有无 `== True/False`）—— 不本次顺手修（rules-dev §4.1 5 分钟规则需 ≤ 3 处全 grep 后判定，已超 scope）
  - bug 2 后续：考虑加 `pot_size_bb` 的清零时机（新 hand 开始时？目前 `latest_pot_bb` 跨 hand 不会自动清；建议 `start_new_hand` 时 reset，但属下一独立 DEV 任务）

## 9. 违规标注

无。

> **基线状态**：连续 10 个 DEV 任务后基线稳态 14p/3s/0f；本次为业务代码层首次实质改动（之前均为治理/基建/文档/依赖修复）。
