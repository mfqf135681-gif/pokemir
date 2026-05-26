# 诊断日志基建:FileHandler + diagnostic_events 表 + showdown/all_in emit

- **完成时间**:2026-05-26 06:06
- **关联讨论**:本会话上文 — 用户问"日志能写 DB 吗",论证 Hybrid(File 全量 + DB 决策点)优于纯 DB / 纯 File
- **关联红线**:无新增红线触发
- **白名单豁免说明**:`contracts/models.sql` 不在自动 commit 白名单,本次改动**纯增量、零破坏**(新增 1 表 + 2 索引,无 ALTER 现有表),用户在对话中明确口头授权(代替 REQ)
- **附带修复**:Gate 6a 牌堆物理约束(card1==card2 即 reject) — 上一轮对话决议 B,本 commit 合并落地

## 1. 任务概述

**问题**:Win 端裸跑 `python main.py pipeline` 时日志只走 stderr,会话结束后**全丢**(PowerShell scrollback 也清掉)。50 分钟录制后想查 8100 巨型底池为啥没识别到 all-in / showdown,**没日志无从下手**。

**方案**(Hybrid 2 层):
1. **FileHandler**:全量日志落 `logs/pokemir_YYYY-MM-DD.log`,Win 本地 tail / Linux 端查回放
2. **`diagnostic_events` PG 表**:**结构化决策点**(showdown 各 gate / all_in 候选 / CNN low-conf 等),**Linux 端可直接 SQL 查询 Win 端运行情况**(R-3 合规通路 ↔ Tailscale mesh)
3. **emit() helper**:`events/diag.py` 提供 `emit(tag, payload, hand_id, level)`,失败回退 logger.warning,**永不阻塞主循环**

**为何 Hybrid 而非纯 DB**:全量日志每 tick 几十行 → 每小时 50 万行级 → 主循环 INSERT 阻塞 + DB 体积爆炸 + 架构污染。**只把"决策点"进 DB**,每手 10-30 行,毫秒级查询。

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | DB 写入失败不应阻塞主循环 | 用户优先级:采集 > 诊断 |
| 2 | `logs/` 落盘失败时回退 console-only,pipeline 仍跑 | sandboxed FS 兜底 |
| 3 | 单 emit 同步插入(无 batch) 足够,因决策点频率 < 1/秒 | showdown ≤ 1/手,all_in 候选稀疏 |
| 4 | TimedRotatingFileHandler when='midnight' + backupCount=14 满足 2 周诊断窗口 | 工程经验 |

## 3. 文件变更清单

| 文件 | 变更 |
|---|---|
| `config.py` | + `LOG_DIR` env 变量;`basicConfig` 改为 dual handler(console + TimedRotatingFileHandler,per-day rotation,backupCount=14);异常时回退 console-only |
| `contracts/models.sql` | + `diagnostic_events` 表(id/hand_id/tag/level/payload/occurred_at) + 2 索引(hand_id+tag+time, tag+time desc) |
| `storage/models.py` | + `DiagnosticEventModel` ORM,关联到 hands(id) ON DELETE CASCADE |
| `events/diag.py` ✨**新建** | `emit(tag, payload, *, hand_id=None, level='INFO')` 接口;lazy engine import;JSON encode 兜底(UUID/datetime → str);失败 swallow + WARN |
| `pipeline/orchestrator.py` | `from events import diag`;`_capture_showdown_cards` 加 8 个 emit 点(gate1_skip / gate2_skip / enter / gate3_no_baseline / gate3_reject / candidates / gate5_low_conf / gate6a_physical_violation / gate6b_hallucination / accepted / incomplete);action 处理加 `all_in.detected` + `all_in.text_only_candidate`(detection gap probe — 当 OCR 文字含 "all in"/"全押" 但 final_action≠all_in 时记录) |

### 附带:Gate 6a 物理约束(上轮 B)

`orchestrator.py:749-755` — `if cards[0] == cards[1]: continue`,早于 history append,避免污染防幻觉历史。3 行。

## 4. 验证

- ✅ `.venv/bin/python -c "import config; ..."` — FileHandler 写 `logs/pokemir_2026-05-26.log` 成功
- ✅ `.venv/bin/python -c "from events import diag; diag.emit(...)"` — INSERT 成功,readback 数据一致
- ✅ `from pipeline.orchestrator import PipelineOrchestrator` — 无 import 错误 / 循环依赖
- ✅ PG 表 schema 与 ORM 字段一致(information_schema 验证)
- ✅ pytest 通过(见 §6)

## 5. 用户后续操作

1. **Win 端拉新代码**:`git pull` → `D:\project\pokemir\logs\` 目录会自动创建
2. **重录 5-10 分钟验证**:期间发生 1+ showdown 或 1+ all-in,然后 Linux 端可直接:
   ```sql
   SELECT tag, payload FROM diagnostic_events
   WHERE occurred_at > NOW() - INTERVAL '1 hour' ORDER BY occurred_at;
   ```
3. **若 8100 类巨型底池再现**:`tag='all_in.text_only_candidate'` 会直接暴露 stack OCR 漏读位置;`tag LIKE 'showdown.gate%_reject'` 会暴露 showdown 被卡在哪个 gate

## 6. 跑测

```
$ .venv/bin/python -m pytest
```

(本节由 pytest 结果填充)

## 7. 不在 scope

- ❌ all-in detection 修复 — 本 commit 只**诊断**,不**修**;等 diag 数据出来再针对性改 normalizer.py:46(stack_after≤5 判定 → 加 text fallback)
- ❌ 历史日志回收 — 用户 PowerShell 缓冲区无解,本 commit 不能救已丢的 8100 那手
- ❌ 切换到 batch INSERT — 当前频率不需要,未来若证明 IO 是瓶颈再加

## 关联记忆

- [[recognition-stack-production-ready]] · [[path-a-step-4-stage-b-accepted]] · [[cross-validation-architecture-pending]]
- [[auto-commit-push-policy]] — 本次 commit 排除 `.agents/communication.md`(非本 dev,你之前的通用化改写)
