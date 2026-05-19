# `latest_pot_bb` 新 hand 开始时清零

- **完成时间**：2026-05-19 02:05
- **关联需求讨论**：无（独立 DEV 修补；source 是前置 change-log §8 标注的待办）
- **关联前次 change-log**：`change-logs/2026-05-18_05-00-00_P0批修monotone比较和process_pot死代码.md` §8 关联待办
- **触发红线**：无
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10

## 1. 任务概述

- **用户原始需求**：Win pull 等待期间的 Linux 小修补 1/2——清掉上一手的 pot 残值
- **bug 来源**：T2 引入 `StateTracker.latest_pot_bb` 后，新 hand 开始时该字段不会自动重置——上一手的 pot 值会污染下一手前几个 action event 的 `pot_size_bb`（直到下一次 `_process_pot` 写入新值）
- **影响窗口**：新手开始 → 第一次 `_process_pot` OCR 成功之间的所有 action event（preflop 早期阶段，可能持续多个 tick）
- **涉及功能模块**：`pipeline/detector.py`（`StateTracker.start_new_hand`）

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 清零时机 = `start_new_hand` 内部，与其它 `_prev_*` 重置同段 | 与现有重置模式（`_prev_action_texts.clear()` / `_prev_community_count = 0`）一致 |
| 2 | `None` 比 `0.0` 更安全 | `_process_seat_actions` 中现有判断 `if self.tracker.latest_pot_bb is not None` → None 让条件 fall through，event 不带错误 pot_size_bb |
| 3 | 测试基线维持 14p/4s/0f | start_new_hand 路径无单元测试覆盖；本改动是纯防御性逻辑 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `pipeline/detector.py` | 修改（+1 行） | `start_new_hand` 末尾加 `self.latest_pot_bb = None` |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**

## 5. 红线合规动作

无触发。

## 6. 测试结果

- **验证路径**：快速验证——单文件单行改动，与 T2 同模块同字段

- **执行**：`.venv/bin/pytest tests/ -v`
  ```
  18 tests collected
  14 passed
   4 skipped
   0 failed
  ```

- **基线对比**：与前次 14p/4s/0f 一致 ✅

- **rules-dev §5.2 判定**：✅ 通过

## 7. 手动操作提醒

无。

## 8. 潜在影响范围

- **正向**：消除 hand 切换时 `pot_size_bb` 字段污染问题；新 hand 第一个 OCR 周期前的 action event `pot_size_bb` 字段 = None（正确表达"尚未读到 pot"），而非沿用上一手的残值
- **行为变化**：无现有测试覆盖该路径；runtime 行为在 Win 端跑生产 pipeline 时生效
- **关联待办**：无（本任务自闭环）

## 9. 违规标注

无。
