# 落地 auto-commit-push 策略到 `.agents/rules-dev.md §5.3`

- **完成时间**：2026-05-22 20:30
- **关联需求讨论**：`requirement-discussions/2026-05-22_20-23-00_DEV结束自动commit和push.md`（confirmed，Q1+Q2 用户 OK 全部推荐）
- **关联前次 change-log**：`change-logs/2026-05-22_20-15-00_新增tools_record_card脚本_fixture录制.md`（同会话上一个 DEV 任务；本任务正好作为首发 auto-commit 验证场景）
- **触发红线**：无（本次属治理规则扩展，REQ 已明示批准 .agents 改动）
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10

## 1. 任务概述

- **用户原始需求**：DEV 模式结束默认自动 commit + push，简化 Linux → Win 同步循环
- **落地范围**：仅写入治理规则；不写 hook 脚本（实际执行仍由 Claude 在每个 DEV 收尾时按 §5.3 步骤跑）
- **涉及功能模块**：`.agents/rules-dev.md`（治理规则）

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | §5.3 紧接 §5.2 验证规则之后是最合适位置 | 验证通过 → commit + push 是验证流程下游 |
| 2 | 不写 git pre-commit hook 实现护栏 | 每个 DEV 任务都涉及不同护栏命中场景；硬编码 hook 难表达"REQ 明示批准"等条件性逻辑；Claude 在自我执行时按规则手动检查更灵活 |
| 3 | "明示批准的 .agents 改动" 作为白名单例外条款写入 | 否则 auto-commit 策略本身无法用 auto-commit 落地（鸡生蛋问题） |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `.agents/rules-dev.md` | 修改（+50 行） | 在 §5.2 末尾后插入 §5.3 完整章节：默认行为声明 + 6 项护栏 checklist + 命中 fallback + commit message 约定 + 永不自动列表 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- 涉及 `.agents/*` 治理规则改动：**是**——REQ 明示批准（自指例外条款见 §5.3 护栏 1）

## 5. 红线合规动作

无触发。.agents 改动按 R-6 之外的治理路径处理（需 REQ confirmed 才能落地，本次已满足）。

## 6. 测试结果

- **验证路径**：快速验证（治理规则文档增量；无可执行代码）

- **执行**：
  - 渲染检查：`.venv/bin/python -c "import markdown; markdown.markdown(open('.agents/rules-dev.md').read())"` → 无异常 ✅（备：项目无 markdown 渲染 CI，本步骤仅 sanity）
  - 章节顺序检查：§5.3 在 §5.2 后、§6 前 ✅
  - 引用一致性：§5.3 引用 §5.2 ✅；引用 REQ 文件路径正确 ✅

- **全 pytest 基线**：未单独跑（.agents 文档改动不影响 Python 测试集）

- **rules-dev §5.2 判定**：N/A（本任务是文档/治理规则，非代码）

## 7. 手动操作提醒

⚠️ **首发踩坑实录**（供未来 auto-commit 调用时参考）：

本次落地 auto-commit 策略当下，**护栏 6 即命中**——用户从更早会话起 `.agents/communication.md` + `.agents/rules-dev.md` 有未 commit 的 generalization 改动（与本次工作无冲突，但护栏规则保护）。

正确处置：**停手报告用户**，列出脏文件分类，让用户选 commit 策略。**未来每会话开始时建议先 `git status` 预检一次**，避免错过这种状态。

## 8. 潜在影响范围

- **正向**：
  - 用户从每次 DEV 后 3 步（add/commit/push）降为 0 步（护栏全过时）
  - Linux → Win 同步循环延迟降到 git 推拉本身的时间
  - 安全护栏明文化，降低我误推风险
- **行为变化**：
  - 从本次后所有 DEV 任务收尾按 §5.3 流程执行
  - REQ / TEST / DOC 模式收尾不自动（明示排除）
- **关联待办**：
  - 用户首次决策本会话 commit 策略后，未来会话每次开始要先 `git status` 预检
  - 如果某次 .agents/* 改动**不**经 REQ 明示批准（例如临时修个 typo），仍按护栏停手——这是有意保留的闸口

## 9. 违规标注

无。
