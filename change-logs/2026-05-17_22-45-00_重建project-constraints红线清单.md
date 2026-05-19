# 重建 project-constraints.md 红线清单（9 条合并版）

- **完成时间**：2026-05-17 22:45
- **关联需求讨论**：`requirement-discussions/2026-05-17_21-00-00_项目红线清单.md`（status: confirmed，阶段 7 方案 B）
- **关联前次 change-log**：无（首次任务）
- **触发红线**：旧 R-5 历史代码保留（删除已有 R-4/R-5 条目 + 改写已有 R-1/R-2/R-3/R-6/R-7）
- **无关红线已检查**：旧 R-1, R-2, R-3, R-4, R-6, R-7

## 1. 任务概述

- **用户原始需求**：阶段 7 用户选方案 B（9 条合并版），DEV 执行重建 `.agents/project-constraints.md`
- **涉及功能模块**：治理基础设施（`.agents/` 目录）
- **相邻任务**：与 `requirement-discussions/2026-05-17_20-30-00_开发工作流与MCP推荐.md` 平行，同属 v0.2.1 治理基础设施建设阶段

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 红线最终清单 = 9 条 R-1~R-9（方案 B） | discussion §阶段 7.4（confirmed） |
| 2 | 退役旧 R-4 / R-5 → 录入"四、已退役红线"区，保留 ID 不复用 | discussion §7.4 + 已有 project-constraints.md "退役标准" |
| 3 | 已有"三、红线收纳标准"段落原文沿用 | 阶段 7 未触及收纳标准 |
| 4 | 我 R-7 Win API 隔离不入红线，转写 `docs/dev-workflow.md`（本任务范围外） | discussion §7.4 "不入项" |
| 5 | 旧 R-1/R-2/R-3/R-6/R-7 改写并重新编号为新 R-4/R-5/R-6/R-7/R-8，不算退役（不占退役 ID 槽位） | discussion §7.4 表格语义 |
| 6 | 验证路径走完整验证（rules-dev §5.1 ② ④ 不满足），但 `.agents/` markdown 改动 pytest 不适用 | rules-dev §5.1 + 工程判断 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `.agents/project-constraints.md` | 重写 | 从 7 条工程纪律向红线 → 9 条合并版（风险向 3 条新增 + 契约向 2 条扩展 + 操作纪律 3 条沿用 + 隐私加固 1 条新增）+ 退役区记录 2 条历史退役 |
| `requirement-discussions/2026-05-17_21-00-00_项目红线清单.md` | 修改 | 元信息块"落地 change-log 路径"回填为本文件 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- 本次仅修改 `.agents/project-constraints.md` 与 `requirement-discussions/` 元信息，不涉 API 或数据模型契约

## 5. 红线合规动作

### 旧 R-5（历史代码保留）触发

- **触发事实**：删除已有 project-constraints.md 中的 R-4 测试阻塞、R-5 历史代码保留两个红线条目；改写已有 R-1/R-2/R-3/R-6/R-7 的描述与合规动作
- **例外援引**：旧 R-5 原条款例外含"用户明确要求"——用户在 REQ 阶段 7 选择方案 B 即明确批准
- **合规执行**：
  1. 已 grep `change-logs/` + `test-reports/` 确认无活跃引用（输出：`✓ change-logs/ 与 test-reports/ 均无 R-4/R-5 引用`），满足已有"退役标准"前置条件
  2. 退役条目已录入新文件"四、已退役红线"区，保留 ID + 退役日期 + 原因 + 重启决策需求
  3. discussion 文档 §7.4 已交叉记录退役

## 6. 测试结果

- **验证路径**：完整验证（rules-dev §5.1 ② 不满足：>20 行；④ 不满足：非纯 UI/单行）→ 但本次为 `.agents/` markdown 文件，pytest 套件不验证治理文件内容
- **测试套件命令**：`pytest tests/ -v`
- **执行情况**：**未执行** —— 当前 Linux dev env 受 PEP 668 保护，pytest 未安装，`pip install --user pytest` 被拒绝（未越权 `--break-system-packages`）
- **manual verification**：
  - ✅ `.agents/project-constraints.md` 写入成功
  - ✅ 文件含 9 条红线（R-1~R-9），与 discussion §7.4 表格一一对应
  - ✅ "四、已退役红线"区记录 旧 R-4 + 旧 R-5，保留 ID 不复用
  - ✅ "三、红线收纳标准"段原文沿用未改
  - ✅ 元信息块更新（复审日期、授权来源指向 discussion 文件）
- **完整环境验证**：建议 Windows 测试机执行 `pytest tests/ -v`，预期与本次改动前一致（无代码改动，应全 pass / skip）

## 7. 手动操作提醒

无。本次变更纯 `.agents/` 治理文件，不涉及代码、依赖、数据库、ROI、Vision 配置。

## 8. 潜在影响范围

- **直接影响**：从下一个 DEV/REQ/TEST 任务起，Router 第三步红线核验输出格式变化（R-1~R-9，不再有旧 R-1~R-7 的 ID）
- **change-log 引用**：后续 change-log 中所有 R-X 引用按新 ID 体系；不要错引旧 ID
- **历史 change-log**：本任务前无 change-log 引用任何 R-X（已验证），无需回填
- **关联待办**：
  - 我 R-7 Win API 隔离 → 待 DEV 任务建立 `docs/dev-workflow.md` 时落地
  - `config.py:9, 13` 硬编码 DB 密码 → R-2 已知违例，建议独立 DEV 任务清理（命名 `清理config.py硬编码凭据`）
  - R-8 识别精度回归保护 → fixture 库（开发工作流讨论 Task #2）完成后回补到红线池

## 9. 违规标注

> ⚠️ **本次违规**：rules-dev §5.2「测试套件必须全通过」未执行 —— **原因**：当前 Linux dev env 受 PEP 668 限制，`pip install --user pytest` 被系统拒绝（未越权使用 `--break-system-packages`，符合"禁止放宽约束"原则）；且本次改动为 `.agents/project-constraints.md` 治理 markdown，pytest 套件不验证此类文件 —— **补救动作**：(1) §6 已记录详细 manual verification 清单（5 项全过）；(2) 已建议在 Windows 测试机执行 `pytest tests/ -v` 作为完整环境基线验证；(3) **下次 DEV 任务开始时主动复盘**：在意图分类标签后追加 `(上次违规已修正)`，并优先在 Windows 测试机跑一次 pytest 建立 baseline。

> **结构性建议**（不在本次任务范围）：rules-dev §5 验证规则可考虑增加"治理文件 / 文档"类的快速验证子路径，避免此类边界场景反复触发违规。这本身需要 REQ 讨论。
