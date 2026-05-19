# MCP 工具安装清单（Task #3）

> 授权来源：`requirement-discussions/2026-05-17_20-30-00_开发工作流与MCP推荐.md`（status: confirmed）
> 落地任务：Task #3
> 落地 change-log：`change-logs/2026-05-18_00-00-00_Task3_MCP配置清单.md`

---

## 1. 总览

| MCP | 包名 | 验证版本 | 用途 | 状态 | 凭据需求 |
|:---|:---|:---:|:---|:---:|:---|
| **github** | (built-in / 现有) | —— | issue/PR/repo 操作 | ✅ 已配置 | GitHub token (已存) |
| **filesystem** | `@modelcontextprotocol/server-filesystem` | 2026.1.14 | 让 Claude 直接读写指定目录的文件 | ⬜ 待装 | 仅授权目录路径 |
| **postgres** | `@modelcontextprotocol/server-postgres` | 0.6.2 | 让 Claude 直接 `SELECT` 验证 ORM 写入 | ⬜ 待装 | DB DSN（含新密码） |
| **context7** | `@upstash/context7-mcp` | 2.2.5 | 拉主流 OSS 库的最新文档喂给 Claude | ⬜ 待装 | 无（可选 Upstash API key） |

---

## 2. 前置环境

| 项 | 要求 | 当前 Linux dev 实测 |
|:---|:---|:---|
| Node.js | ≥ 18 | v24.14.1 ✅ |
| npm | ≥ 9 | 11.11.0 ✅ |
| Claude Code CLI | ≥ 2.x | v2.1.144 ✅ |
| 网络 | 能访问 npm registry | —— |

Windows 端：自行确认 Node.js 安装（推荐用 nvm-windows 或官方 installer）。

---

## 3. 安装命令

### 3.1 filesystem MCP

> 让 Claude 通过 MCP 协议读写指定目录。比 Bash + Read 更结构化、流式、原生协议传输。

#### Linux dev (VPS)

```bash
claude mcp add filesystem -s local -- \
  npx -y @modelcontextprotocol/server-filesystem /home/alxe/project/pokemir
```

#### Windows test machine（Git Bash）

```bash
claude mcp add filesystem -s local -- \
  npx -y @modelcontextprotocol/server-filesystem "/c/pokemir-test"
```

**scope 选择**：`local`（仅当前 user 看；不污染 project / 不入 git）

**⚠️ 授权范围**：只指定**项目根目录**。**禁止**指定 `~` / `/` / `/home/alxe`——会让 Claude 读到 `~/.ssh/`、`.env`、密码管理器导出文件等敏感内容。

### 3.2 postgres MCP

> 让 Claude 直接执行 SQL 查询（包括 `SELECT * FROM hands LIMIT 10` 类验证），不用你手动贴结果。

#### Linux dev (本机无 PG，可暂跳)

#### Windows test machine（PG 应当可达）

⚠️ **前置**：先完成"凭据清理"任务（已 done），并在 `.env` 中填入**新密码**（即 R-2 合规动作要求的轮换后密码）。

```bash
# 临时变量装载 DSN，避免命令历史暴露
read -s -p "Enter rotated DB password: " PG_PASS && echo
DSN="postgresql://poker_user:${PG_PASS}@localhost:5432/poker_assistant"
claude mcp add postgres -s local -- npx -y @modelcontextprotocol/server-postgres "$DSN"
unset PG_PASS DSN
```

**或**手写一次（密码进 bash history 风险）：

```bash
claude mcp add postgres -s local -- \
  npx -y @modelcontextprotocol/server-postgres \
  "postgresql://poker_user:<新密码>@localhost:5432/poker_assistant"
```

**🔴 安全注意**：DSN 含明文密码，会被写入 `~/.claude.json`。该文件应：
- 权限 `chmod 600`（验证：`stat -c %a ~/.claude.json` 应为 `600`）
- 不进任何 git 仓
- 不上传公共云盘备份

postgres MCP 默认 **read-only**（参见包文档），但仍应使用最小权限 DB user。

### 3.3 context7 MCP

> 注入 cv2 / easyocr / sqlalchemy / transformers 等主流 OSS 库的当前版本 API 文档，缓解我训练数据滞后问题。

#### Linux + Win 均同样命令

```bash
claude mcp add context7 -s local -- npx -y @upstash/context7-mcp
```

**scope 选择**：`local`（项目级隔离；pokemir 与 flordate 各 own 各的 context7 配置，互不污染）

**可选**：注册 Upstash 账号取 API key 提高 rate limit（不需要也能跑，free tier 已足够小项目）。如需用：

```bash
claude mcp add context7 -s local -e CONTEXT7_API_KEY=ctx7sk-xxxxxxxx -- \
  npx -y @upstash/context7-mcp
```

---

## 4. 安装后验证

```bash
claude mcp list
```

应看到 4 项：

```
github     ...
filesystem  command: npx, args: [-y, @modelcontextprotocol/server-filesystem, /home/alxe/project/pokemir]
postgres    command: npx, args: [-y, @modelcontextprotocol/server-postgres, postgresql://...]  ← 仅 Win 端
context7    command: npx, args: [-y, @upstash/context7-mcp]
```

**重启 Claude Code session** 让新工具生效（当前会话不会自动看到新 MCP）。重启后我应能看到：

- `mcp__filesystem__*` 系列工具（read_file / write_file / list_directory 等）
- `mcp__postgres__*` 系列工具（query / list_tables / describe_table 等）
- `mcp__context7__*` 系列工具（resolve-library-id / get-library-docs）

---

## 5. 安全考量

| MCP | 风险点 | 缓解 |
|:---|:---|:---|
| **filesystem** | 误把 `~/.ssh` / `.env` / 密码管理器目录加入授权 → 密钥泄露 | 只授权项目根目录；不加 `~` 或 `/`；定期 `claude mcp list` 检查授权范围 |
| **postgres** | DSN 含明文密码写入 `~/.claude.json` 后任何能读该文件的进程可见 | 用专用最小权限 DB account；`chmod 600 ~/.claude.json`；不进 git / 不入云备份 |
| **context7** | 拉网络文档，Claude 可能基于过时/不准确文档给建议 | 当参考用，不盲信；契约和实际代码仍是权威；高风险决策另行验证 |

---

## 6. 删除 / 更新

```bash
claude mcp remove filesystem
claude mcp remove postgres
claude mcp remove context7
```

或直接编辑 `~/.claude.json` 的 `mcpServers` 段（删完重启 Claude）。

---

## 7. 配置片段（手动编辑参考）

如果不想用 CLI，可直接编辑 `~/.claude.json` 添加：

```json
{
  "mcpServers": {
    "github": {
      "...": "（沿用现有配置，不动）"
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem",
               "/home/alxe/project/pokemir"]
    },
    "postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres",
               "postgresql://poker_user:<新密码>@localhost:5432/poker_assistant"]
    },
    "context7": {
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp"]
    }
  }
}
```

> 改完后退出当前 Claude Code session 重启生效。

---

## 8. 故障排查

| 现象 | 可能原因 | 处理 |
|:---|:---|:---|
| `claude mcp add` 报 EACCES | npx 缓存权限问题 | `npm cache clean --force` 后重试 |
| filesystem MCP 工具不出现 | 路径不存在 / 权限缺失 / scope 错 | `ls <path>` 验证；`claude mcp list` 看 scope |
| postgres MCP 工具不出现 | DSN 错误 / PG 未启动 | 终端跑 `psql "$DSN" -c '\dt'` 验证；查 PG 日志 |
| context7 报 timeout | 网络/上游服务问题 | 重试；检查 `npx -y @upstash/context7-mcp` 能否独立启动 |
| 重启 Claude Code 后仍看不到工具 | session 缓存 | 完全退出（Ctrl+D 或关终端窗口）再启 |
| MCP 工具频繁 timeout | 大文件 / 慢查询 | 限制 filesystem 读小文件；postgres 加 `LIMIT` |

---

## 9. 与项目工作流的关系

| 任务 | 装哪几个 MCP 提效最大 |
|:---|:---|
| 调试管道 / 看日志 | filesystem（Win 端） |
| 验证 ORM 写入 / 看 hands 表 | postgres（Win 端） |
| 调 cv2/easyocr 新接口 | context7（Linux + Win） |
| 看 PR / 创建 issue | github（已装） |
| Win 端调 fixture（Task #2 启动后） | filesystem（Win 端） |

---

## 10. 相关文件

| 文件 | 作用 |
|:---|:---|
| `~/.claude.json` | Claude Code 全局配置 + MCP servers |
| `tools/setup-mcps.sh` | 一键安装脚本（见 §11） |
| `docs/dev-workflow.md` | 同步工作流（Task #1） |
| `.agents/project-constraints.md` | 项目红线（R-1 ~ R-10） |
| `requirement-discussions/2026-05-17_20-30-00_开发工作流与MCP推荐.md` | 本任务源讨论（阶段 6 装哪些 + 阶段 8 怎么装） |

---

## 11. 一键安装脚本（setup-mcps.sh）

> 推荐方式。脚本位置：`tools/setup-mcps.sh`。授权：`requirement-discussions/2026-05-17_20-30-00_开发工作流与MCP推荐.md §阶段 8.4`（confirmed）。

### 11.1 用途

幂等安装 pokemir 用得到的 5 个 MCP：

| MCP | scope | 装法 |
|:---|:---|:---|
| context7 | local | `npx -y @upstash/context7-mcp` |
| github | **user**（特例） | `npx -y @modelcontextprotocol/server-github` |
| semgrep | local | `semgrep mcp`（需先 `.venv/bin/pip install semgrep`，禁 pipx 触发 R-10） |
| filesystem | local | `npx -y @modelcontextprotocol/server-filesystem $PATH` |
| postgres | local | `npx -y @modelcontextprotocol/server-postgres $DSN` |

**scope 策略**：默认 `local`（项目级隔离，与 flordate 不互相污染）。**github 特例 `user`**：因 GitHub token 是账号级凭据、跨项目天然共用，独立给每个项目装一份反而徒增管理成本——不在"项目级隔离"语义范畴内。如脚本检测 github 已存在（无论 scope）会自动跳过，不重复 add。

**不装**：cloudbase / chrome-devtools / Google Drive（flordate 装了但 pokemir 不相关）。

### 11.2 使用

```bash
# 默认：filesystem 指向 $(pwd)，postgres DSN 交互输入
bash tools/setup-mcps.sh

# 自定义：env var 预设
POKEMIR_FS_PATH=/c/pokemir-test \
POKEMIR_PG_DSN='postgresql://poker_user:xxx@localhost:5432/poker_assistant' \
bash tools/setup-mcps.sh
```

### 11.3 前提

- Node.js + npm + npx（Linux 已 v24.14.1 ✓）
- Claude Code CLI 在 PATH（已 v2.1.144 ✓）
- 若装 semgrep MCP：`semgrep` 在 PATH。装法 `.venv/bin/pip install semgrep`（**禁** `pipx install` / `pip install --user`，触发 R-10）。未装则脚本跳过 semgrep + 警告。

### 11.4 幂等性

脚本检查 `claude mcp list`，已装的跳过。可重复执行。

### 11.5 更新 / 删除

```bash
claude mcp remove <name>   # 然后重跑脚本
```

或参考 §6。

### 11.6 与 §3 的关系

§3 是"分步参考"，本节是"一键替代"。两者命令语义等价；新机器优先用本节。
