# setup-mcps.sh scope 策略修正 v2：默认 local（与 flordate 项目隔离习惯一致；github 特例保留 user）

- **完成时间**：2026-05-18 02:30
- **关联需求讨论**：`requirement-discussions/2026-05-17_20-30-00_开发工作流与MCP推荐.md`（阶段 8 confirmed 之 scope 策略续接修正——通过本次会话中的 REQ 快速讨论 + AskUserQuestion 用户选 A "全 local"，github 子选项选 1A "保留 user"）
- **关联前次 change-log（续接型）**：`change-logs/2026-05-18_00-30-00_R10立项与setup-mcps脚本.md`（首次创建脚本时设 user scope；本次根据用户"项目级隔离"理念修正）
- **触发红线**：无
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10

## 1. 任务概述

- **用户原始需求**：把 `setup-mcps.sh` 的 scope 默认值改为 `local`（与 flordate 一致），github 因 token 账号级保留 `user`（特例）
- **触发场景**：REQ 指导本次 MCP 安装走完整准备时，发现脚本 scope 与用户"项目级隔离"理念冲突。本次为修正动作
- **涉及功能模块**：dev infra（`tools/setup-mcps.sh` + `docs/mcp-setup.md`）；不动业务代码

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | scope 调整范围：context7 + semgrep 改 local；github 保留 user；filesystem + postgres 本来就 local 不动 | 本会话 REQ 指导 §1.1 + 用户选 A + 1A |
| 2 | github 保 user 的合理性：GitHub token 是账号级凭据，跨项目天然共用 | 1A 子选项说明 |
| 3 | docs/mcp-setup.md 需同步 §3.3 和 §11.1 的 scope 列 + 加 github 特例说明 | rules-dev §3 同源契约一致 |
| 4 | 不动 `~/.claude.json` 现有配置（脚本是声明式安装路径，不主动覆盖） | rules-dev §3 最小改动；用户已有 github user-scope 会被脚本检测到 already-installed 跳过 |
| 5 | 验证 = 完整 pytest（rules-dev §5.1 多文件 + 非纯 UI/单行；走完整路径） | rules-dev §5.1 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `tools/setup-mcps.sh` | 修改 | 文件头注释 MCP 清单 scope 标注同步；脚本主体 context7 `-s user`→`-s local`，semgrep `-s user`→`-s local`；github 保留 `-s user` 并加 `[intentional: account-level token, cross-project]` 说明；重排顺序为 github 在前（user 段）+ 其余在后（local 段），加清晰段注释 |
| `docs/mcp-setup.md` | 修改 | §3.3 context7 命令 `-s user`→`-s local` + 注解改为"项目级隔离"语义；§11.1 表格 scope 列更新（context7/semgrep=local；github 标记 **user**（特例））+ 加 "scope 策略" 段说明 github 特例理由 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- 不涉 API / 数据模型 schema

## 5. 红线合规动作

无触发。本次纯 dev infra（脚本 + docs）改动。

## 6. 测试结果

- **验证路径**：完整验证（rules-dev §5.1 ① 多文件 / ④ 非纯 UI）

- **执行 1**：`bash -n tools/setup-mcps.sh` → ✅ shell 语法 OK
- **执行 2**：`grep -E "skip_or_install \w+ -s"` 验证 scope 分布：
  - github → `-s user`（1A 特例保留）
  - context7 → `-s local`
  - semgrep → `-s local`
  - filesystem → `-s local`（不变）
  - postgres → `-s local`（不变，在单独 if 分支内）
- **执行 3**：`.venv/bin/pytest tests/ -v`
  ```
  17 tests collected
  11 passed / 6 skipped / 0 failed
  耗时 25.37s
  ```
- **与基线对比**：与前次连续基线 11/6/0 完全一致
- **rules-dev §5.2 判定**：✅ 通过

## 7. 手动操作提醒

⚠️ **手动操作**（REQ 指导 Step 2-3 的延续）：

1. **用户在 Linux dev 终端跑脚本**（pokemir 目录内）：
   ```bash
   cd /home/alxe/project/pokemir
   bash tools/setup-mcps.sh
   ```

2. **预期交互**：
   - context7 → `+ installing context7...`（**新装到 local scope**）
   - github → `⏭ already installed, skipping`（user-scope 已存在被识别）
   - semgrep → `⚠️ semgrep not in PATH — skipping`（venv 内未装 semgrep；如需启用先 `.venv/bin/pip install semgrep`）
   - filesystem → `+ installing filesystem...`（**新装 local scope，路径 `/home/alxe/project/pokemir`**）
   - postgres → 提示输入 DSN；**Linux dev 本机无 PG，直接 Enter 跳过**

3. **完整退出 Claude Code session 重启**（Ctrl+D 两次，或关终端窗口）后，下次会话能用 `mcp__filesystem__*` / `mcp__context7__*` 工具

4. **验证**：跑 `claude mcp list`，应在 pokemir 目录下看到 4 个 connected（claude.ai Google Drive + github + context7 + filesystem）

5. **Win 测试机**：rsync 同步代码到 `C:\pokemir-test` 后，在 Git Bash 跑：
   ```bash
   cd /c/pokemir-test
   POKEMIR_FS_PATH=/c/pokemir-test bash tools/setup-mcps.sh
   ```
   提示 DSN 时输入凭据清理后的新密码

## 8. 潜在影响范围

- **正向影响**：
  - scope 策略与用户在 flordate 建立的"项目级隔离"习惯一致——切换项目时配置完全独立
  - 未来新项目要装 context7/semgrep 时，需按各自项目独立装一次（**这是预期行为**，不是 regression）
  - github 保 user：跨项目共享一份 token 配置，避免每项目重复
- **配置影响**：
  - 用户 `~/.claude.json` 现有 user-scope `github` **不动**（脚本检测已存在跳过）
  - flordate 现有 local-scope context7/semgrep **不动**（在 flordate 的 projects 子配置里，与 pokemir local-scope 独立）
- **关联待办**（REQ 指导剩余步骤）：
  - Step 2：用户在 Linux dev 跑脚本（见 §7）
  - Step 3：重启 Claude Code session 让 MCP 生效
  - Step 4：Win 端跟进（rsync + 重跑脚本）
  - Step 5（可选）：装 semgrep 启用 semgrep MCP

## 9. 违规标注

无。

> **上次违规残留状态**：仍消解（连续 6 个 DEV 任务 11 passed + 6 skipped + 0 failed 基线稳定）。
