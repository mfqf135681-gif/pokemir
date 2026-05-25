# Infra 清理: .gitignore 模式失效修 + MCP postgres DSN 同步

- **完成时间**：2026-05-25 02:00
- **关联前次 change-log**：`change-logs/2026-05-25_01-28-00_R3扩展_VPS_Tailscale_PG监听.md`
- **触发红线**：**无**(均为 infra cleanup,不涉敏感表 / 不改契约 / 不动模型)
- **无关红线已检查**：R-1 到 R-10 全部

## 1. 任务概述

收尾 [[path-a-step-4-stage-a-accepted]] 中记账的两个"非阻塞遗留":
1. `.gitignore` 第 32-34 行尾部注释当成 pattern 一部分,导致 `models/`、`tests/output/`、`tests/fixtures/_pending/` 三个模式全部失效
2. `~/.claude.json` 中 MCP postgres server 的 DSN 含旧密码 hash,与现 `.env` 不匹配 → `mcp__postgres__query` 工具 auth 失败

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | gitignore 不支持尾部行内注释(必须独立行) | `man gitignore`:"A blank line matches no files... otherwise, the line is treated as a single pattern." 整行作为 pattern |
| 2 | ~/.claude.json 在 git 仓库外,不入版本控制,改它不触发 R-2 凭据入 git | `ls -la ~/.claude.json` 路径与项目 .gitignore 都验证 |
| 3 | MCP server 在 session 启动时加载 args,运行时不重读配置 | `ps -ef` 看到旧密码 hash 已 baked 进 spawn argv;kill 后 Claude Code 不自动 respawn → 效果延后到下次 session |
| 4 | tracked 的两个老 .pth 文件(craft_mlt_25k.pth / english_g2.pth)不动,只让新文件不被纳入追踪 | 防止 Win 端 git pull 后 working tree 文件被删导致 EasyOCR 重新下载,scope 外 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `.gitignore` | 修改(+8/-3 行)| 把 3 个尾部注释 pattern 拆为 `# 注释 \n pattern` 两行形式;新增 NB 说明防止再犯 |
| `~/.claude.json` | 修改(项目外)| jq 替换 `.projects["/home/alxe/project/pokemir"].mcpServers.postgres.args[2]` 的密码片段为 .env 当前值;备份在 `~/.claude.json.bak-<timestamp>` |

### 附带修复（5 分钟规则）

无(主动忍住没顺手 `git rm --cached models/easyocr/*.pth`,因为会删 Win 端 working tree 文件,需独立处理)。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**

## 5. 红线合规动作

无红线触发:
- R-2(凭据入 git):MCP DSN 在 `~/.claude.json`,git 仓库外,且原本就只是同步密码到当前值;.env 仍是凭据唯一源(`.gitignore` 已忽略)
- R-3(数据通路):MCP postgres → `localhost:5432`,VPS 自机内部,无变化
- R-7(配置一致性):`.gitignore` 修复反而增强配置正确性
- 其他红线均不相关

## 6. 测试结果

- **验证路径**:完整验证 + 副作用扫描

- **`.gitignore` 修复验证**:
  ```
  $ git check-ignore -v models/easyocr/zh_sim_g2.pth
  .gitignore:36:models/	models/easyocr/zh_sim_g2.pth     # exit=0
  $ git check-ignore -v tests/output/foo.png
  .gitignore:38:tests/output/	tests/output/foo.png         # exit=0
  $ git check-ignore -v tests/fixtures/_pending/x.json
  .gitignore:40:tests/fixtures/_pending/	tests/fixtures/_pending/x.json  # exit=0
  ```
- **`git status` 不再显示** `models/easyocr/zh_sim_g2.pth` ✓

- **MCP DSN 同步验证**:
  - `~/.claude.json` 中 DSN 已替换为 .env 当前值(脱敏后:`postgresql://poker_user:<REDACTED>@localhost:5432/poker_assistant`)
  - 备份文件存在:`~/.claude.json.bak-*`
  - **MCP tool 实测**:本次 session 内 `mcp__postgres__query` 仍 fail(预期 — server 已 baked 旧 args)
  - 下次 Claude Code session 启动会用新 DSN respawn

- **rules-dev §5.2 判定**:✓ 通过(本次为 markdown / json 改,无 python 代码改,pytest 不重跑)

## 7. 手动操作提醒

⚠️ **下次 Claude Code session 启动后**(自动生效,无需手动操作):
- MCP postgres server 会用新 DSN respawn
- `mcp__postgres__query` 工具应恢复可用
- 测试方法:任意 sql 查询如 `SELECT COUNT(*) FROM hands;` 应返回数字而非 auth fail

⚠️ **不需要 Win 端做任何事**:
- `.gitignore` 改动 Win pull 后无破坏(只让未来新 `models/` 下文件不被追踪;现有 tracked 文件继续保留)

## 8. 潜在影响范围

- **正向**:
  - 未来下载到 `models/` 下任何文件不会污染 git status
  - `tests/output/` (test_capture 截图) 与 `tests/fixtures/_pending/` (Win 端未审 fixture) 也终于真生效
  - MCP postgres tool 下次 session 起恢复,我可以继续用 SQL 查 Win pipeline 落库
- **行为变化**:本 session 内 MCP postgres tool 不可用(killed 后未 respawn);用户与我可走 host psycopg2 直连或等下次 session

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(用户明示"开发模式,处理你列出的两个问题")
- 产出物:`.gitignore` 修 + `~/.claude.json` 修 + 本 change-log
- 红线状态:无触发(R-1..R-10 全 N/A)
- 5-min 规则:命中 1 个潜在附带修(`git rm --cached models/easyocr/*.pth`),主动评估有副作用后**忍住没做**,记入第 8 节 follow-up
