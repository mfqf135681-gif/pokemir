# Linux dev 装 postgres + postgres MCP 必要性

- **讨论时间**：2026-05-18 03:00（阶段 1-5）；2026-05-18 03:30（阶段 6 用户决策）
- **状态**：confirmed
- **用户决定**：Q1 方案 D Docker Compose + 项目内 volume；Q2 = Yes 补 psycopg2-binary
- **落地 change-log**：`change-logs/2026-05-18_03-30-00_Linux dev装postgres docker compose基建.md`
- **触发红线**：本讨论中**重新评估**了 R-3（云 PG 是否触发）——最终选 D 方案不外传数据，**R-3 不触发**
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10
- **关联既有讨论**：首次讨论；与 `2026-05-17_20-30-00_开发工作流与MCP推荐.md` 相邻（同治理基础设施阶段，但主题不同：那个讲 MCP 装哪些，本讨论讲 PG 装不装+怎么装）

---

## 阶段 1：需求理解与复述

**核心问题**：Linux dev VPS 上 PostgreSQL + postgres MCP 该不该装？

**业务背景**：
- 项目目标平台 Win，**生产手牌数据**只能落 Win 端 PG
- Linux 是开发主力，目前没装 PG → `test_storage.py` 永远 3 skip
- Linux 端 postgres MCP 也因 DB 不可达跳过未装

**约束条件**：
- R-10 工件隔离——任何持久化目录必须在项目内
- R-2 凭据——Linux 上 PG 密码与 Win 端独立
- Phase 2 存储层代码已成型但实战未跑过

---

## 阶段 2：现状分析

### 2.1 环境实测

| 项 | 状态 |
|:---|:---|
| Docker | 未装 |
| apt postgresql | 未装 |
| 端口 5432 | 空 |
| `.venv` 内 sqlalchemy + asyncpg | ✅ |
| `requirements.txt` psycopg2-binary | ❌ **缺失（latent bug）** |
| 磁盘 | 26GB available |

### 2.2 关键差距

- `test_storage.py` 3 用例永久 skip → 存储层覆盖率 0
- 我（Claude）无法直接 SELECT DB → ORM 写入对不对全靠用户贴日志
- ORM 改动只能 push 到 Win 验证 → R-6（ORM-SQL 同步）触发时无本地验证手段
- 缺 psycopg2-binary → 装 PG 后必撞错

---

## 阶段 3：方案设计

候选方案：

| 方案 | 描述 | R-10 合规 | 红线触发 |
|:---|:---|:---|:---|
| A 不装 | 保持现状 | N/A | 无 |
| B apt postgresql | sudo apt + 建 user/db | 例外（系统级） | 无 |
| C Docker 单容器 | docker run 单容器 | 例外（docker daemon）+ volume 项目内 | 无 |
| **D Docker Compose + 项目 volume** | docker-compose.yml 入 git；volume `.docker-data/postgres/` | **严格合规** | 无 |
| E SQLite | 改 ORM 适配 SQLite | 合规 | **触发 R-5** |
| **F（用户提案）云 PG 共享** | 两端共连云 managed PG | N/A | **触发 R-3 精神层面** |

---

## 阶段 4：方案对比与推荐

### 对比矩阵

| 维度 | A 不装 | B apt | C docker run | **D docker compose** | E SQLite | **F 云 PG** |
|:---|:---|:---|:---|:---|:---|:---|
| 工作量 | 0 | 30 min | 30-60 min | 30-60 min | 数小时 | 30 min |
| 持续成本 | 0 | 0 | 0 | 0 | 0 | **¥30-50/月** |
| dev/prod 隔离 | N/A | 完全分离 | 完全分离 | **完全分离** | 完全分离 | ❌ 默认共用 |
| R-3 触发 | 无 | 无 | 无 | 无 | 无 | **触发（精神层）** |
| 网络依赖 | 无 | 无 | 无 | 无 | 无 | 强依赖 |
| 跨机器一致性 | N/A | 漂移 | 一致 | **一致** | 漂移 | 一致 |
| 团队/CI 友好 | N/A | 低 | 中 | **高** | 中 | 中 |
| 跨平台体感 | 不一致 | 漂移 | 一致 | **一致** | 漂移 | 一致 |

### F（云 PG）方案专项评估——R-3 触发性

| 视角 | 判定 | 理由 |
|:---|:---|:---|
| 字面 | ❌ 不触发 | R-3 触发条件特指"HTTP 客户端"，PG 协议 TCP/5432 不在字面范围；"用户显式配置内网地址"——DSN 在 .env 显式指向云地址等于授权 |
| 精神 | ✅ 触发 | R-3 立条核心是"手牌私有不外传"。云 PG 显然是把数据放外面 |

→ 选 F 必须先 REQ 扩展 R-3 条款（治理基础设施改动 + 数据脱敏方案 + 用户隐私同意）。工作量翻倍。

### 推荐：**D Docker Compose**

⚠️ **风险点**：
- 需 VPS root sudo 装 docker（一次性成本）
- PG 容器开机自启占内存 200-300MB（不用可 stop）
- Linux dev 上 PG 密码 ≠ Win 端生产 PG 密码（两库本独立）

🔧 **前置条件**：
- VPS sudo 权限
- 接受 ~200MB Docker image + ~50MB volume 增长
- 同步补 psycopg2-binary 到 requirements.txt

---

## 阶段 5：待确认清单

### Q1 选哪个方案？

🎯 选项：A 不装 / B apt / C docker run / **D docker compose**（推荐）/ E SQLite / F 云 PG 共享

💡 推荐 D：pokemir 当前单人 + 单 Win，没有共享需求；F 主要价值在 pokemir 不存在但代价仍在；零持续成本；R-3 不触发；将来真要多端共享再升 F 不晚。

### Q2 补 psycopg2-binary 缺失？

🎯 选项：**Yes**（推荐）/ No

💡 推荐 Yes：Q1 选 B/C/D 任一必撞 `No module psycopg2` → 不补必败。

---

## 阶段 6（2026-05-18 03:30）：用户决策记录

用户回复"开发，接受建议，按方案D执行"——双确认：

| 项 | 选择 |
|---|---|
| Q1 装 PG 方案 | **D Docker Compose + 项目内 volume** |
| Q2 psycopg2-binary | **Yes（补）** |

### 落地任务定义（DEV）

| # | 动作 | 文件 |
|:---|:---|:---|
| 1 | 创建 `docker-compose.yml`（postgres:15，volume `.docker-data/postgres/`，端口 127.0.0.1:5432） | 新建 |
| 2 | 更新 `.gitignore` 加 `.docker-data/` | 修改 |
| 3 | 更新 `requirements.txt` 加 `psycopg2-binary>=2.9` | 修改 |
| 4 | 更新 `.env.example` 加 `POKEMIR_DB_PASSWORD=change-me` | 修改 |
| 5 | 更新 `docs/dev-workflow.md` 加 §2.4 Linux 数据库 Docker Compose 配置 | 修改 |
| 6 | 输出 change-log | 新建 |

### 手动操作（DEV 完成后用户执行）

详见 change-log §7：
1. sudo apt install docker.io docker-compose-plugin
2. 设 `.env` 的 `POKEMIR_DB_PASSWORD` + chmod 600
3. docker compose up -d
4. `.venv/bin/pip install -r requirements.txt`（装 psycopg2-binary）
5. `bash tools/setup-mcps.sh` 装 postgres MCP（DSN 提示时输入）
6. `.venv/bin/pytest tests/ -v` 验证（应见 14 passed）

### F 方案保留场景

将来若进入团队化 / 多 Win 测试机 / 多平台部署阶段，可重启 REQ 升级到 F 方案——需配套 R-3 条款扩展。
