# Fix Stage B Pipeline Crash: num_seats + Position enum + start_new_hand FK protection

- **完成时间**：2026-05-25 18:10
- **关联前次 change-log**：`change-logs/2026-05-25_17-40-00_seat方向修正_顺时针左邻为seat1_加8座layout.md`
- **关联 bug 报告**:用户实战 Win 端 `python main.py pipeline --profile party_poker_8` 后连环 crash
- **触发红线**：**R-7（ROI 配置一致性 — num_seats 计算来源)**
- **无关红线已检查**：R-1 到 R-6, R-8, R-9, R-10

## 1. 任务概述

用户配齐 seat_1 + seat_7 (8 座的左右下邻位) 跑 pipeline,出现 3 个连环错误:

```
ValueError: 'S0' is not a valid Position
↓
ForeignKeyViolation: hand_id ... not present in hands (因为 hand 没插 DB)
↓
ValueError: Hand ... not found (因为 hand 没插 DB)
```

根因 3 个 + 衍生 1 个:
1. `compute_positions` 用 `len(seat_regions)=2`(只配齐 2 个)做模运算,落入 fallback 返回 `S0/S1`
2. `Position` enum 只含 6 个值,缺 `UTG+1 / MP+1 / HJ`,8-max + 9-max 标签部分无法 lookup
3. `_start_new_hand` 把 DB 插入放在 Position 计算之后 → Position 抛错后 hand 没入库,但 tracker.current_hand 已设
4. 后续 tick 用 stale current_hand.id 写 action_events → FK 违反

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | profile JSON 的 `num_seats` 字段是"桌型 8/9 座的总位数"权威来源 | party_poker_9.json 早就有 |
| 2 | 标准扑克 position 含 UTG+1 / MP+1 / HJ,Python StrEnum 用 value="UTG+1" + name=UTG1 (避 "+" 非法 name) | 行业惯例 + Python enum 语法限制 |
| 3 | DB 插入 hand 是 FK 锁的源头;Position metadata 是 best-effort,允许失败后 update 补 | FK 约束在 schema 上 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `events/models.py` | 修改(+5/-0 行)| `Position` enum 加 `UTG1 = "UTG+1"`, `MP1 = "MP+1"`, `HJ = "HJ"`;docstring 标注 6/8/9 max 标准 |
| `capture/roi.py` | 修改(+22/-7 行)| `TableROIs` 加 `num_seats: int = 0` 字段;`from_dict` 从 profile 读;`compute_positions` 改用 `self.rois.num_seats`,configured_indices 过滤,button 未知或表型未知时返回空 dict (不再 fallback S0/S1) |
| `pipeline/orchestrator.py` | 修改(+22/-5 行)| `_start_new_hand` 重排:DB hand_repo.create **先入**,失败立刻 `current_hand=None` + return;Position 计算后用 update 补 seats metadata(best-effort),单个 seat Position lookup 失败用 try/except skip |

### 附带修复（5 分钟规则）

无(刻意忍住不改 Position enum 名称风格,UTG+1→UTG1 是 Python 语法必需,无法避免)。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**否**
- DB schema 不变;action_events.position 字段仍是 TEXT(允许 enum 之外字符串,但运行时只能写 enum 范围内值)

## 5. 红线合规动作

**R-7（ROI 配置一致性)** 触发:
- [x] `num_seats` 字段是 profile JSON 既有约定,本次首次被代码权威使用
- [x] partial 配置场景明确语义:configured seat 才有 position label,未配置 seat 不参与
- [x] button 未知场景:不再 fallback 通用 label,改为不返回 position(下游 try/except 处理)

**R-10(原子性 / FK 完整性)** 隐含:
- [x] hand 插入失败后 tracker.current_hand = None,防 cascading FK violation
- [x] best-effort metadata update 包了 try/except,避免一处错误污染整条链路

## 6. 测试结果

- **pytest**:14 passed / 3 skipped / 0 failed ✓
- **smoke**(模拟用户场景 8 座 + 2 seat 配齐 + button at 0):
  ```
  positions: {1: 'SB', 7: 'CO'}
  Position('SB') → SB ✓
  Position('CO') → CO ✓
  ```
- **rules-dev §5.2 判定**:✓ 通过

## 7. 手动操作提醒

⚠️ **Win 端用户**:

```powershell
cd D:\project\pokemir
git pull
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker_8
```

预期改后行为:
- hand 立刻入库(看到 `Hand <uuid> — hero: ... — ids: ...`)
- 即使 button OCR 没检测到(`No button detected via OCR, using seat 0 as default`),pipeline 继续工作
- action_events 在玩家有动作时正常落库
- 不再 FK violation,不再 Position ValueError

## 8. 潜在影响范围

- **正向**:
  - Stage B 在部分配置状态下也能落库验证(不强求 8 全配齐)
  - Position enum 完整,支持所有 6/8/9-max 标准位置
  - hand DB 入库提前,杜绝 FK violation cascade
- **行为变化**:
  - button 未知时 hand.seats 不再含通用 S0 标签,而是 empty dict;path B 统计时需注意 "seats 不全 ≠ 无效手牌"
- **关联待办**:
  - 用户重跑 pipeline 验证修复
  - stage B B3 真实 action_events 落库验证

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(bug 修复,无 REQ 因为是实施期发现的代码缺陷)
- 产出物:3 文件改 + 本 change-log + 1 commit 待推
- 红线状态:R-7 触发(num_seats 来源切换),合规;其他 N/A
- pytest:14 passed,无新增 fail
