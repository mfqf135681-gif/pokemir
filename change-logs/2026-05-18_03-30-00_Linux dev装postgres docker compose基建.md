# Linux dev 装 postgres 基建：Docker Compose + 项目内 volume + psycopg2-binary 补缺

- **完成时间**：2026-05-18 03:30
- **关联需求讨论**：`requirement-discussions/2026-05-18_03-00-00_Linux dev装postgres与MCP必要性.md`（confirmed，Q1=D Docker Compose，Q2=Yes 补 psycopg2-binary）
- **关联前次 change-log**：无续接；**相邻任务** = `change-logs/2026-05-17_23-45-00_清理config.py硬编码凭据.md`（R-2 凭据轮换；本次配套设新 dev PG 密码）+ `change-logs/2026-05-18_02-30-00_setup-mcps脚本_v2_scope改local.md`（MCP 安装链中 postgres MCP 这一环本次补齐）
- **触发红线**：无（设计上严格规避——R-2 走 env var 引用，R-10 数据 volume 项目内）
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10

## 1. 任务概述

- **用户原始需求**：按 REQ 讨论方案 D 落地 Linux dev 端本地 PG（Docker Compose + 项目内 volume），同时补 psycopg2-binary 缺失
- **涉及功能模块**：dev infra（Docker 配置 + Python 依赖 + 文档），**不动业务代码**
- **相邻任务**：
  - `change-logs/2026-05-17_23-45-00_清理config.py硬编码凭据.md`（R-2 凭据基础建立；本次为 Linux dev 配套独立密码）
  - `change-logs/2026-05-18_02-30-00_setup-mcps脚本_v2_scope改local.md`（MCP 装链；本次为 Linux 端 postgres MCP 的前置准备）

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 用户决定 = D 方案 + Yes 补 psycopg2 | `2026-05-18_03-00-00_*.md §阶段 6`（confirmed）|
| 2 | docker-compose.yml 用 `${POKEMIR_DB_PASSWORD:?...}` 引用 env var 不硬编码 | R-2 合规设计原则 |
| 3 | 数据 volume `.docker-data/postgres/` | R-10 合规：项目内 + 即将 gitignored |
| 4 | 端口绑定 `127.0.0.1:5432:5432`（不暴露公网） | 安全默认 |
| 5 | psycopg2-binary（不是 psycopg2 source） | 无需 libpq-dev 系统依赖；R-10 角度更友好 |
| 6 | `docs/dev-workflow.md` 加 §2.4（在"首次配置"段下）；§8 相关文件表加 docker-compose.yml 行 | rules-dev §3 最小改动 |
| 7 | Docker 本身需 sudo apt 装——属用户手动操作；DEV 不执行 | rules-dev §3 + §9 手动操作清单 |
| 8 | 验证：pytest 应仍 11/6/0（docker 未启，test_storage 继续 skip）；用户跑完手动操作后变 14/3/0 | DB 不可达走 skip 不算 fail |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `docker-compose.yml` | **新增** | postgres:15 服务；env var 引用密码；端口 127.0.0.1:5432；volume `.docker-data/postgres/`；healthcheck；restart unless-stopped；含详细注释（授权来源 / 用法 / R-10 + R-2 合规说明） |
| `.gitignore` | 修改（+3 行） | 加 `.docker-data/`（注释：R-10 项目内数据 volume，不入 git） |
| `requirements.txt` | 修改（+1 行） | Database 段加 `psycopg2-binary>=2.9` + 行内注释说明用途 |
| `.env.example` | 修改（+4 行） | 加 POKEMIR_DB_PASSWORD（必填占位 `change-me`，附 openssl rand 生成建议）+ POKEMIR_DB_USER / POKEMIR_DB_NAME（可选，有默认） |
| `docs/dev-workflow.md` | 修改 | §2.3 后插入 §2.4 "Linux VPS 端：数据库（Docker Compose）"——7 步操作流程 / 日常操作表 / 与 Win 端关系表 / 安全注意；§8 相关文件表加 docker-compose.yml 行 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- 数据 schema 不变（`contracts/models.sql` 未动）；本次仅是把"在哪跑 PG"具体化，DB 内部结构不变

## 5. 红线合规动作

### R-2 凭据硬编码——设计期规避

**规避事实**：`docker-compose.yml` 中 `POSTGRES_PASSWORD: ${POKEMIR_DB_PASSWORD:?ERROR...}` 走环境变量引用，**绝不**硬编码字面密码。`.env.example` 中 `POKEMIR_DB_PASSWORD=change-me` 是占位符符合 R-2 例外。

→ **未触发**。

### R-10 项目工件隔离——设计期合规

**合规事实**：
- 数据 volume `.docker-data/postgres/` 在项目目录内（pwd-relative path in compose）
- 已加入 `.gitignore`，不污染 git
- Docker daemon 本身是 system tool，命中 R-10 例外条款"使用系统级工具本身不在此限"

→ **未触发**。

### 其他红线核验未触发

- R-3 数据外传：本机 5432 监听 127.0.0.1，无外网；不触发
- R-5 数据模型契约：未改 `contracts/models.sql`
- R-6 ORM 同步：未改 `storage/models.py`
- R-8 视觉识别配置：无关

## 6. 测试结果

- **验证路径**：完整验证（多文件 + 涉及依赖声明）

- **执行**：`.venv/bin/pytest tests/ -v`
  ```
  17 tests collected
  11 passed
   6 skipped
   0 failed
  耗时 23.83s
  ```

- **与基线对比**：连续 7 个任务 11/6/0 稳定；本次仅添 dev infra 文件，无业务代码触碰 → 零回归 ✅

- **YAML 语法验证**：`python -c "yaml.safe_load(open('docker-compose.yml'))"` → ✅
- **rules-dev §5.2 判定**：✅ 全通过（skip 不算 fail）

- **预期升级**：用户跑完 §7 手动操作后，pytest 应变 **14 passed / 3 skipped / 0 failed**（3 storage 测试解锁，仅 2 capture + 1 card_recognizer 继续 skip）

## 7. 手动操作提醒

⚠️ **手动操作**（用户在 Linux dev VPS 终端执行；详见 `docs/dev-workflow.md §2.4`）：

1. **装 Docker**（一次性，需 sudo）：
   ```bash
   sudo apt update
   sudo apt install -y docker.io docker-compose-plugin
   sudo usermod -aG docker $USER
   # 退出重登 shell（或 newgrp docker）让权限生效
   ```

2. **设密码**到 `.env`（用 openssl 随机生成）：
   ```bash
   cd /home/alxe/project/pokemir
   echo "POKEMIR_DB_PASSWORD=$(openssl rand -hex 16)" >> .env
   chmod 600 .env
   ```

3. **同步 .env 的 DSN**（让 DB_DSN / DB_DSN_SYNC 使用刚生成的密码）：
   ```bash
   # 编辑 .env 中两行 DSN，把 `poker_pass` 占位换成 step 2 的实际密码
   ```

4. **启动 PG**：
   ```bash
   docker compose up -d
   docker compose ps   # 等到 STATUS = healthy
   ```

5. **装 psycopg2-binary**（已加入 requirements.txt）：
   ```bash
   .venv/bin/pip install -r requirements.txt
   ```

6. **验证 storage 测试**（应 14 passed）：
   ```bash
   .venv/bin/pytest tests/ -v
   ```

7. **装 postgres MCP**（让我能 SELECT 查 DB）：
   ```bash
   POKEMIR_PG_DSN="postgresql://poker_user:<step2 密码>@localhost:5432/poker_assistant" \
     bash tools/setup-mcps.sh
   # 重启 Claude Code session 后我下次能用 mcp__postgres__query
   ```

8. **`~/.claude.json` 权限**（postgres MCP 装入后 DSN 进文件）：
   ```bash
   stat -c %a ~/.claude.json    # 应为 600
   chmod 600 ~/.claude.json     # 若不是
   ```

## 8. 潜在影响范围

- **正向**：
  - test_storage.py 3 用例从永久 skip → 跑通；存储层覆盖率从 0 → 100%
  - 我下次会话能用 `mcp__postgres__*` 直接 SELECT 验证 ORM 写入、字段映射、JSONB 行为
  - ORM 改动（R-6 触发场景）可在 Linux 端本地验证，不必每次 push 到 Win
  - dev/prod 物理隔离：Linux 改动只影响本机 DB，永远不污染 Win 端真实手牌数据
- **环境引入**：
  - Docker 容器 + image（~150MB 系统级）
  - `.docker-data/postgres/` volume（初期 ~50MB，随数据增长）
  - `psycopg2-binary` ~3MB 装入 venv
- **关联待办**：
  - Win 测试机 PG 设置：用户在 Win 上独立装 PG（不走 Docker 也可），密码独立
  - 装完后可考虑用 alembic 把 `contracts/models.sql` 转成 migration（独立 DEV 任务）

## 9. 违规标注

无。

> **上次违规残留状态**：仍消解（连续 7 个 DEV 任务 11/6/0 基线稳定）。
