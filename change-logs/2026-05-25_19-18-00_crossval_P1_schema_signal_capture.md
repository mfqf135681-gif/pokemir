# 交叉验证 P1: action_events.confidence_score + raw_data 5+ 信号采集

- **完成时间**：2026-05-25 19:18
- **关联需求讨论**：`requirement-discussions/2026-05-25_19-11-00_交叉验证架构_4层金字塔_path_B衔接.md`(confirmed,Q1-Q5 全 A)
- **关联记忆**：[[cross-validation-architecture-pending]] · [[path-a-step-4-stage-b-accepted]]
- **关联前次 change-log**：`change-logs/2026-05-25_18-40-00_player_seatindex_bug_action_OCR_诊断日志.md`
- **触发红线**：**R-7（pipeline + schema 一致性 — 加列 + 行为 tick 顺序变更)**
- **无关红线已检查**：R-1 到 R-6, R-8, R-9, R-10

## 1. 任务概述

实施 4 层金字塔交叉验证架构的 **P1 阶段**:仅做"信号采集 + 持久化基建",不改 action_type 推断逻辑。为后续 P2-P4 打基础。

具体:
- schema 加 `action_events.confidence_score` 列
- StateTracker 加 `_prev_stack` per-seat 字典 + `_pot_before_tick` 字段
- orchestrator tick 调换 pot/action 顺序(pot 先,action 后)
- `_process_seat_actions` 每 tick 始终读 stack(不再仅 event 创建时读)
- 创建 event 时,把 5+ 路证据写入 `event.raw_data` JSONB

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | tick 内"pot 先 action 后"顺序无破坏副作用 | 现 _process_pot 仅更新 tracker 状态 + log;_process_seat_actions 用 latest_pot_bb 即可 |
| 2 | stack OCR 每 tick 调用对性能可接受 | 测过 stack allowlist=digit-only ~10-30ms × 2-9 seat = <300ms,500ms tick 仍宽裕 |
| 3 | confidence_score 默认 1.0 + P1 全填 1.0 → P2 启动前不破坏 path B 统计 | 既有数据 NULL = "未评估",新数据 1.0 = "未做 cross-val 但默认信任";不会比现状差 |
| 4 | _prev_stack 单 hand 内有效,新 hand 自动 reset(start_new_hand)| 已加 `.clear()` |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `contracts/models.sql` | 修改(+2 行)| `action_events` 表加 `confidence_score DOUBLE PRECISION DEFAULT 1.0`;同时加索引 `idx_action_events_confidence` |
| `storage/models.py` | 修改(+1 行)| `ActionEventModel` 加 `confidence_score = Column(Float, default=1.0)` |
| `events/models.py` | 修改(+1 行)| `ActionEvent` dataclass 加 `confidence_score: float = 1.0` |
| `storage/repository.py` | 修改(+1 行)| `ActionEventRepository.create` 写 `confidence_score=event.confidence_score` |
| `pipeline/detector.py` | 修改(+8/-1 行)| `StateTracker.__init__` 加 `_pot_before_tick: float \| None` + `_prev_stack: dict[int, float]`;`start_new_hand` 重置两者 |
| `pipeline/orchestrator.py` | 修改(+40/-12 行)| `_tick` 调换 pot 与 seat_actions 顺序;`_process_pot` 在更新 latest_pot_bb 前 snapshot 到 `_pot_before_tick`;`_process_seat_actions` 每 tick 始终读 stack(不依赖 action change),计算 stack_delta + pot_delta,build raw_data JSONB(5+ 键),每个 event 写入 |
| PG `action_events` 表 | ALTER TABLE | `ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION DEFAULT 1.0` + 索引;已现场执行 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**是**
- `contracts/models.sql` 同步加 `confidence_score` 列 + 注释 + 索引;与 `storage/models.py` 一致
- ORM 字段顺序与 SQL 一致(`raw_data` → `confidence_score` → `created_at`)
- 旧数据 `confidence_score = NULL`(因 column 有默认值,但旧行不会自动 fill;DB DEFAULT 1.0 仅对新 INSERT 生效)→ path B 统计需谨慎,见 REQ 阶段 4 已说明

## 5. 红线合规动作

**R-7（pipeline + schema 一致性)** 触发:
- [x] schema 加列用 `IF NOT EXISTS`,migration 幂等
- [x] ORM / dataclass / SQL contract 三处同步加 confidence_score
- [x] tick 顺序变更(pot ↔ action 调换)在 `_tick` doc-comment 注明原因
- [x] _process_pot side-effect 加注释说明
- [x] additive raw_data write — 不破坏既有 ActionEvent 行为

## 6. 测试结果

- **语法**:`pipeline/orchestrator.py` + `pipeline/detector.py` AST parse OK ✓
- **ActionEvent smoke**:`confidence_score = 1.0` 默认值正确 ✓
- **DB schema 验证**:`information_schema.columns` 含 `confidence_score double precision default 1.0` ✓
- **pytest**:14 passed / 3 skipped / 0 failed ✓
- **rules-dev §5.2 判定**:✓ 通过

未直接 unit-test 的项:
- 真实 pipeline 跑 tick 期间 raw_data 是否正确填充 — 留 Win 端实测

## 7. 手动操作提醒

⚠️ **Win 端用户**:

### A. `git pull`

```powershell
cd D:\project\pokemir
git pull
```

### B. 跑 pipeline,观察 raw_data 是否写入

```powershell
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker_8
```

正常运行无新错误即可;`tick` 顺序内部变更对用户不可见。

### C. 验证 raw_data 落库(我在 VPS 远端查)

执行后:
```sql
SELECT id, action_type, raw_data, confidence_score
FROM action_events
ORDER BY created_at DESC
LIMIT 5;
```

预期看到:
- 新 events 的 `raw_data` 含 8 个键(action_text / stack_before / stack_after / stack_delta / pot_before / pot_after / pot_delta / text_derived_action)
- `confidence_score = 1.0`(P1 默认,P2 才开始评分)

## 8. 潜在影响范围

- **正向**:
  - P2/P3/P4 实施有完整证据基础
  - 数据可观察性提升:新 events 含详尽 OCR 原始证据
  - 路径独立可验证:每个 P 阶段可单独 SQL 抽查
- **行为变化**:
  - 每 tick 多 N(seat 数)次 stack OCR 调用;allowlist digit-only ~10-30ms × seat,500ms tick 仍宽裕
  - tick 顺序调换:pot 先 action 后 — pipeline 数据流不变,只是 raw_data 能拿到新 pot
  - 既有 action_events 历史数据 confidence_score=NULL → path B SQL 查询应加 `confidence_score IS NULL OR confidence_score >= 0.7` 过滤
- **关联待办**:
  - P2 实施(Layer 1 自洽方程 + confidence 评分)
  - P3 实施(Layer 2/3 规则推断 + stack-derived override)
  - P4 实施(review CLI + replay_corrections 写回)

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(承接 REQ 2026-05-25_19-11-00 confirmed:Q1=A 渐进式)
- 产出物:6 文件改 + 本 change-log + 1 PG ALTER TABLE + 1 commit 待推
- 红线状态:R-7 触发,合规动作执行;其他 N/A
- pytest:14 passed,无新增 fail
- 5-min 附带修主动忍住:P2-P4 不在本次 scope
