# A: player_name OCR 过滤 + C: P2 Layer 1 物理方程 confidence 评分

- **完成时间**：2026-05-25 20:05
- **关联 REQ**：`requirement-discussions/2026-05-25_19-11-00_交叉验证架构_4层金字塔_path_B衔接.md`(confirmed,推进 P2)
- **关联记忆**：[[cross-validation-architecture-pending]] · [[path-a-step-4-stage-b-accepted]]
- **关联前次 change-log**：`change-logs/2026-05-25_19-27-00_roi_config_all_seats_batch_mode.md`
- **触发红线**：**R-7（pipeline 逻辑一致性 — 加 confidence 评分 + ID 抓取过滤)**
- **无关红线已检查**：R-1 到 R-6, R-8, R-9, R-10

## 1. 任务概述

stage B 8 座全跑数据后发现 2 个问题,合并修:

### A: player_name="跟注" 数据污染(immediate fix)

用户实战数据显示 12 个 action_event 的 player_name="跟注"(中文动作关键字)。根因:
- WePoker 中 ID 与 action 显示在**同像素**(头像上方)
- `_capture_player_ids` 在 hand-start 抓 ID,但 hand 转场时该位置可能仍残留上一手动作文字
- OCR 把"跟注"当昵称缓存,后续 events 全用这个错的 player_name

### C: P2 Layer 1 物理方程 + confidence 评分

按 [[cross-validation-architecture-pending]] REQ 推进 P2:
- 单玩家 tick 中 `pot_delta` 应 ≈ `stack_delta`(扑克守恒律)
- 自洽 → confidence=1.0;偏离按比例打分;阈值 0.7 以下进入 review 类(REQ Q3)
- 不改 action_type(P3 才做);P2 仅评分

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | `ActionRecognizer.parse()` 对"跟注"等关键字返回非 None;对真实昵称("林道八"等)返回 None | parser 实现 + 实测 |
| 2 | 单玩家 tick 是常态(500ms tick,玩家行动间隔 2-5s 通常)| 实战 25 events 分析 |
| 3 | fold / check 类动作 stack_delta 必须 ≈ 0,否则推断错误 | 扑克规则 |
| 4 | 阈值 0.7 是 REQ confirmed 的 review 分界 | REQ Q3 confirmed |
| 5 | 多玩家 tick 情况下 confidence 会偏低 — 这是预期,P3 会用 Layer 2 扑克规则细化 | 设计本意 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `pipeline/orchestrator.py` | 修改(+11/-2 行)| `_capture_player_ids` 加 ActionRecognizer.parse 过滤,parsed != None 即视为 OCR 失败 + 不写 id_map;event create 后调 `compute_confidence` 写 `event.confidence_score` |
| `events/normalizer.py` | 修改(+49/-1 行)| 新增 module-level `compute_confidence(action_type, stack_delta, pot_delta) -> float` 函数;fold/check 看 stack ≈ 0;chip-contributing 看 \|pot - stack\| 绝对差 + 相对偏离;返回 [0.3, 1.0];文档化 4-tier 阈值 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**否**
- `action_events.confidence_score` schema 列在 P1 已建好,本次仅写入逻辑

## 5. 红线合规动作

**R-7（pipeline + 数据一致性)** 触发:
- [x] ID 过滤:防 OCR 上下文污染玩家昵称,数据完整性保护
- [x] P2 评分 additive,不改 action_type 推断
- [x] confidence_score 默认 1.0(P1),仅本次开始评分;旧数据不动

## 6. 测试结果

- **compute_confidence smoke**(代表性 case):
  ```
  fold + stack=0       → 1.0  ✓
  fold + stack=50      → 0.3  ✓ (suspicious,推断错)
  call match (50/50)   → 1.0  ✓
  call close (50/53)   → 0.9  ✓ (OCR 噪声)
  call off 20% (50/65) → 0.7  ✓ (borderline)
  call off 100% (50/100)→ 0.3 ✓
  call all None        → 0.5  ✓
  raise 309/189 (实例) → 0.3  ✓ (DGMT168 example 标低)
  raise 36/32 (实例)   → 0.7  ✓ (DGMT168 raise 36 example 标 borderline)
  ```
- **pytest**:14 passed / 3 skipped / 0 failed ✓
- **rules-dev §5.2 判定**:✓ 通过

## 7. 手动操作提醒

⚠️ **Win 端用户**:

### A. `git pull`

```powershell
cd D:\project\pokemir
git pull
```

### B. 跑 pipeline 观察新数据

```powershell
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker_8
```

挂几手观察预期变化:
- `player_name="跟注"` **不再出现**(改 Player_<sidx> fallback)
- 部分 events confidence_score < 1.0(自动反映多信号不一致)
- log 中可能出现 `_capture_player_ids: seat_X got action-text 'XXX', skipping` 的 debug

### C. VPS 端查 confidence 分布(我做)

```sql
SELECT
  ROUND(confidence_score::numeric, 1) AS conf_bucket,
  COUNT(*) AS n,
  action_type
FROM action_events
WHERE timestamp >= NOW() - INTERVAL '30 minutes'
GROUP BY conf_bucket, action_type
ORDER BY conf_bucket, action_type;
```

预期看到:
- 大多数 fold/check confidence=1.0
- 部分 call/bet/raise confidence < 1.0(数据问题被 surface)

## 8. 潜在影响范围

- **正向**:
  - player_name 完整性恢复(无"跟注"污染)
  - confidence_score 开始反映数据质量(为 path B 统计过滤提供基础)
  - 不改 action_type(逻辑 P3 才做,数据稳定性高)
- **行为变化**:
  - `_capture_player_ids` 部分 seat 可能 ID OCR 失败 → fallback Player_<sidx>;后续 hand 重试
  - confidence < 0.7 的 events 进入 "review pending" 状态(P4 工具未做,目前仅 SQL 查询)
- **关联待办**:
  - P3 Layer 2/3:扑克规则推断 + stack-derived action_type override
  - P4 Layer 4:review CLI + replay_corrections 写回
  - 用户更多观察数据 → P2 evaluate accuracy

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(承接 REQ 19-11 + 用户 A+C 决议)
- 产出物:2 文件改 + 本 change-log + 1 commit 待推
- 红线状态:R-7 触发,合规;其他 N/A
- pytest:14 passed,无新增 fail
- 5-min 附带修主动忍住:P3 / P4 留下次
