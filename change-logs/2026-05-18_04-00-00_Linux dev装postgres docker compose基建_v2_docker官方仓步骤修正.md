# dev-workflow.md §2.4 Docker 安装步骤修正 v2：用 Docker 官方仓替换错误的 docker.io 路径

- **完成时间**：2026-05-18 04:00
- **关联需求讨论**：`requirement-discussions/2026-05-18_03-00-00_Linux dev装postgres与MCP必要性.md`（confirmed，Q1=D）
- **关联前次 change-log（续接型）**：`change-logs/2026-05-18_03-30-00_Linux dev装postgres docker compose基建.md`（前次写 §2.4 时 Docker 安装命令用了 Ubuntu 自带仓的错误路径 `docker.io + docker-compose-plugin`；本次按 Docker 官方文档修正）
- **触发红线**：无
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10

## 1. 任务概述

- **用户原始需求**：上一会话本 turn 用户实际跑 §2.4 Step 1 命令 `sudo apt install -y docker.io docker-compose-plugin` 失败（`Unable to locate package docker-compose-plugin`）；REQ 模式通过 context7 查 Docker 官方文档拉到正确步骤；用户实测装通后回"装好了"——本次切 DEV 把正确步骤沉淀到 docs
- **bug 本质**：`docker-compose-plugin` 是 Docker 官方仓（download.docker.com）专属包，**不在** Ubuntu 自带仓（archive.ubuntu.com）中。前次 change-log 写命令时未查官方文档，凭印象写了错路径
- **涉及功能模块**：仅 docs（`docs/dev-workflow.md §2.4`），不动业务/基建代码
- **相邻任务**：本次纯文档修正；与 `change-logs/2026-05-18_03-30-00_*.md`（PG 基建初版）续接

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 修正方向 = 完整 7 substep（清理冲突 → 装前置 → GPG key → apt 仓 → apt update → 装 docker-ce 全套 → 加组 → 验证） | 上一 turn REQ 通过 context7 拉的 Docker 官方文档（/docker/docs `docs/engine/install/ubuntu.md`）|
| 2 | 用户实测版本：Docker 29.5.1 / Compose v2.x / hello-world 通过 | 用户贴的终端输出 |
| 3 | 结构调整：把 Docker 装从原 numbered list 的"Step 1"剥离为独立 `#### A.` 小节，避免 heredoc 内容被 list 的 3-space 缩进污染 | 用户已被 heredoc 缩进坑卡死过一次（A.4 命令"无反应"故障） |
| 4 | PG 步骤 numbered list 删原 Step 1（Docker 装）后 1→6 顺移；编号引用从 "step 2" 改为 "step 1" | 结构调整自然结果 |
| 5 | 加 §B 故障排查小节，沉淀本会话两轮 REQ 教训（heredoc 顶格 + 国内 mirror + newgrp 重登 + docker-compose-plugin 缺失自查）| 防同坑再踩 |
| 6 | docs 内含 heredoc 代码块时，**整段需在 markdown 顶层**（不被 numbered list 缩进），保证用户直接复制不掺前导空格 | 已被实证踩坑 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `docs/dev-workflow.md` | 修改（§2.4 结构重组 + Docker 步骤详化） | (1) `#### 步骤` 拆为 `#### A. 装 Docker` + `#### B. 故障排查` + `#### C. PG 配置步骤`；(2) A 段含 7 个 substep 的完整 Docker 官方装法（清理冲突 / 前置工具 / GPG key / apt 仓 + heredoc 顶格警告 / apt update + 装全套 / 加 docker 组 + newgrp / 验证）；(3) B 段 5 行故障排查表（heredoc 卡死 / Unable to locate / 国内 mirror / permission denied / compose 子命令缺失）；(4) C 段沿用原 PG 步骤但删除错的 Docker Step 1 + 编号 2-7 顺移为 1-6；(5) "授权来源"段加 Docker 官方文档 cross-link |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- 仅 docs 修正，不涉 API / 数据模型 / ORM

## 5. 红线合规动作

无触发。

## 6. 测试结果

- **验证路径**：完整验证（rules-dev §5.1 ② 不严格满足——改动行数 >20；④ 非纯 UI/单行）

- **执行 1**：`.venv/bin/pytest tests/ -v`
  ```
  17 tests collected
  11 passed
   6 skipped
   0 failed
  耗时 25.04s
  ```
- **执行 2**：结构 grep 检查
  ```
  ### 2.4 Linux VPS 端：数据库（Docker Compose）
  #### A. 装 Docker（一次性系统级前置）
  #### B. 故障排查（A 步常见坑）
  #### C. PG 配置步骤（A 完成后）
    1. 设密码
    2. 启动 PG 容器
    3. 同步 .env 的 DSN
    4. 装 psycopg2-binary
    5. 验证 storage 测试
    6. 装 postgres MCP
  ```
  → ✅ 三段式（A 前置 / B 排查 / C 主流程）结构清晰；C 段 6 步顺序正确
- **与基线对比**：连续 8 个 DEV 任务 11/6/0 稳定；本次纯 docs 改动，零业务代码影响
- **rules-dev §5.2 判定**：✅ 通过

- **真实验证（用户实测）**：用户已实际跑通 Docker 29.5.1 + Compose v2.x + hello-world——证明本次 docs 中的 7 substep 命令在 Ubuntu 24.04 noble 上有效

## 7. 手动操作提醒

⚠️ **手动操作**（用户继续 §2.4 §C 步骤）：

Docker 装好后，跑 §C 步骤继续 PG 配置：

```bash
cd /home/alxe/project/pokemir

# C.1 设密码
echo "POKEMIR_DB_PASSWORD=$(openssl rand -hex 16)" >> .env
chmod 600 .env

# C.2 启 PG（数据 volume 自动建在 .docker-data/postgres/）
docker compose up -d
docker compose ps   # 等 healthy

# C.3 编辑 .env，把 POKEMIR_DB_DSN / POKEMIR_DB_DSN_SYNC 的 'poker_pass' 占位换成 C.1 实际密码

# C.4 装 psycopg2-binary
.venv/bin/pip install -r requirements.txt

# C.5 验证 storage 测试
.venv/bin/pytest tests/test_storage.py -v   # 应见 3 passed

# C.6 装 postgres MCP
POKEMIR_PG_DSN="postgresql://poker_user:<C.1 密码>@localhost:5432/poker_assistant" \
  bash tools/setup-mcps.sh

# 重启 Claude Code session 让 postgres MCP 生效
```

## 8. 潜在影响范围

- **正向**：
  - 后续任何参考 §2.4 装 Docker 的开发者（包括以后另一台机器 / 同事 / 我自己 follow up）走的是 Docker 官方推荐路径，不会再撞 docker-compose-plugin 缺失
  - §B 故障排查表沉淀两轮 REQ 教训：heredoc 顶格 / 国内 mirror / 包名查错——下次同类问题查 §B 即可
  - 结构 A/B/C 划分让"系统装 Docker"与"项目配 PG"概念分离，新读者认知负担减小
- **未受影响**：业务代码 / contracts / ORM / 现有测试 / 之前已合规的红线
- **关联待办**：
  - 用户继续 §C 步骤跑通 PG（预计 5-10 分钟）
  - 跑完后 pytest 应升级到 14p/3s/0f（解锁 3 个 storage 测试）
  - 装完 postgres MCP 后下次会话我能 `mcp__postgres__query` 直接 SELECT 验证 DB

## 9. 违规标注

无。

> **上次违规残留状态**：仍消解（连续 8 个 DEV 任务 11p/6s/0f 基线稳定）。
