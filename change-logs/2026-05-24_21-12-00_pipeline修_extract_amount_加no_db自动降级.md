# pipeline 修 `_extract_amount` 真 bug + 加 no-db 自动降级

- **完成时间**：2026-05-24 21:12
- **关联需求讨论**：`requirement-discussions/2026-05-24_20-54-00_PathA第3步_Win端pipeline全链路smoke.md`（pending,正在迭代）
- **关联前次 change-log**：`change-logs/2026-05-24_20-59-00_main加profile参数_默认改party_poker.md`（path A 第 3 步第 1 个崩溃修）
- **触发红线**：无
- **无关红线已检查**：R-1 到 R-10 全无触发

## 1. 任务概述

Path A 第 3 步 A 探底第 2 轮,用户 traceback 反馈 3 个问题:

| # | 错误 | 性质 | 修法 |
|:---:|:---|:---|:---|
| 1 | `_extract_amount() missing 1 required positional argument: 'text'` | **真代码 bug** | 加 `@staticmethod` 装饰器 |
| 2 | `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd6 in position 61` | 环境问题（PG 没装,libpq 错误用 Win 中文 locale 编码,psycopg2 mangle 成 UTF-8 解码错）| **自动降级到 no-db 模式** |
| 3 | hand_repo / event_repo 写入失败连锁错（Tick failed 每秒）| #2 衍生 | 同 #2 修后自动消失 |

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | `_extract_amount` 真应是 `@staticmethod`（不使用 self,且 AMOUNT_RE 是类变量）| 代码 review:第 21 行 `AMOUNT_RE = re.compile(...)` 是类变量,函数体只访问它 |
| 2 | orchestrator 之前两处调用方式 `ActionRecognizer._extract_amount(text)` 本来就是 staticmethod 期待用法 | grep 显示 :212 和 :249 两处都这么调,只是函数签名错了 |
| 3 | Win 端无 PG 时,smoke 测试仍应能跑（识别 / 状态机 / 日志输出能验证 pipeline 逻辑）| Path A 第 3 步 A 探底目标 = pipeline 不崩 + 主循环跑,不强求落库 |
| 4 | DB 探测放 `__init__` 一次性,启动时报警告即可 | 比每 tick 试图连 + 每 tick 报错噪声小得多 |
| 5 | 0xd6 UnicodeDecodeError 根因是 PG 不可达 + Win 中文 locale + psycopg2 错误处理 bug | 多种证据：用户 Win 中文环境,test_storage 也出过相同错,Linux 测试机相同 PG 状态从来用错误消息（"connection refused"）|

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `recognition/actions.py` | 修改（+1/-1 行）| `_extract_amount` 加 `@staticmethod` + 用 `ActionRecognizer.AMOUNT_RE` 替代 `self.AMOUNT_RE` |
| `pipeline/orchestrator.py` | 修改（+27/-7 行）| `__init__`: 新增 `_probe_db()` 自检 + `self._db_enabled` 标志 + 不可达时打 WARNING log; `_tick`: db = SessionLocal() if self._db_enabled else None + commit/rollback/close 全部 `if db is not None`; `_start_new_hand` / `_end_current_hand` / `_process_community_cards` / `_process_seat_actions` / `_shutdown` 各处 `repo.create()/update()` 调用全 gate 在 `if db is not None` |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- ActionRecognizer 公共 API 不变（`_extract_amount` 是内部辅助,前缀 `_`）
- pipeline runtime 行为变化:有 PG 时与之前完全一致,无 PG 时跳过 DB 操作

## 5. 红线合规动作

无触发：
- R-1 / image-only：未变（仍只截图）
- R-3：未变（PG 是本地依赖,no-db 模式则连本地都不写,更安全）
- R-5 / R-6：DB schema / ORM 都不变

## 6. 测试结果

- **验证路径**：完整验证（多文件 + 跨模块 + 行为变化）

- **smoke 1**：`_extract_amount` staticmethod 调用 → `ActionRecognizer._extract_amount('CALL $2.50')` 返回 2.5 ✓
- **smoke 2**：orchestrator import 无错（Linux 无 PG 也行,因为 __init__ 现在容错）
- **pytest sanity**：`pytest tests/ -q --ignore=tests/test_recognition_fixtures.py` → 14 passed / 3 skipped / 0 failed ✓
- **fixture pytest 在 Linux 跳过**（CNN 模型 Win 独有）

- **rules-dev §5.2 判定**：✓ 通过

## 7. 手动操作提醒

⚠️ **Win 用户**:
1. `git pull` 拉本次改动
2. 重跑 `python main.py pipeline`
3. 预期:
   - 启动后打印 `WARNING ... PostgreSQL unreachable — running in NO-DB mode`（确认 no-db 自动生效）
   - **不再有** `_extract_amount` TypeError 刷屏
   - **可能仍有 0xd6 UnicodeDecodeError**——如果 _probe_db 在某些路径漏了 catch（罕见但可能;若有请贴）
   - pipeline 应该**安静地跑**,有 hand 切换时打 hand-start log
4. 观察 2-5 分钟看是否还有别的 bug 浮现,贴回新 log

## 8. 潜在影响范围

- **正向**：
  - path A 第 3 步前两个 bug 修复;pipeline 可以无 PG smoke 测
  - `_extract_amount` 真 bug 修了,任何用 pot / stack 的代码都能跑（之前一调就崩）
  - no-db 模式让无 PG 部署也能 demo pipeline（教学 / 演示场景额外受益）
- **行为变化**：
  - **有 PG 的环境**：行为完全不变（_probe_db 通过,_db_enabled=True,后续所有 db 操作正常）
  - **无 PG 的环境**：启动一次性警告,后续不再为 DB 写入崩溃
- **关联待办**：
  - 后续可能浮现的 bug:seats=[] 导致 `_process_seat_actions` / `_capture_player_ids` / `_detect_button_position` 全 noop;中文动作 OCR 失败;hand-start 触发条件细节
  - 用户**真要数据落库**时,需独立讨论 Win 端 PG 部署选型

## 9. 违规标注

无。
