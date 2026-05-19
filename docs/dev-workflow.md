# 开发工作流（Linux VPS 主开发 / Windows 本地测试）

> 授权来源：`requirement-discussions/2026-05-17_20-30-00_开发工作流与MCP推荐.md`（status: confirmed，阶段 6）
> 落地任务：Task #1
> 落地 change-log：`change-logs/2026-05-17_23-00-00_Task1同步工作流配置.md`

---

## 1. 拓扑

| 角色 | 位置 | 路径示例 | 职责 |
|:---|:---|:---|:---|
| **开发主力** | Linux VPS | `~/project/pokemir/` | 改代码、跑纯逻辑测试、git push |
| **测试机** | Windows 本地办公机 | `C:\pokemir-test\` | 跑屏幕捕获 + 识别 + 端到端管道 |
| **同步方向** | Linux VPS → Windows | rsync over SSH | Win 主动拉，VPS 不主动推 |

**为什么 Win 主动拉**：拓扑 B 下 Win 是本地、Linux 是远程公网。若 VPS 主动推 → 需 Win 公网暴露 SSH（不安全）。反过来 Win 主动拉 → Win 走出站连接（标准做法）。

---

## 2. 首次配置（一次性）

### 2.1 Windows 端

1. 装 **Git for Windows**（<https://gitforwindows.org/>）—— 自带 SSH client、rsync、Git Bash
2. 打开 Git Bash
3. 生成 SSH 公私钥：
   ```bash
   ssh-keygen -t ed25519 -C "win-pokemir-test"
   ```
4. 把生成的 `~/.ssh/id_ed25519.pub` **追加** 到 VPS 的 `~/.ssh/authorized_keys`
5. 测试连接（应返回 `ok` 无密码提示）：
   ```bash
   ssh your-vps-user@your-vps-host echo ok
   ```
6. 首次 clone（用 git，建立初始工作副本）：
   ```bash
   cd /c
   git clone ssh://your-vps-user@your-vps-host/home/your-vps-user/project/pokemir.git pokemir-test
   cd pokemir-test
   ```
   或从 GitHub origin clone —— 任一都可，后续 rsync 都会覆盖到最新。

7. 配置 sync 脚本所需环境变量。在 `~/.bashrc`（Git Bash 启动加载）中追加：
   ```bash
   export POKEMIR_VPS_HOST=your-vps-host
   export POKEMIR_VPS_USER=your-vps-user
   export POKEMIR_VPS_PATH=/home/your-vps-user/project/pokemir/
   ```
   重新打开 Git Bash 生效。

8. 装 Python 3.13 + 项目依赖（一次性）：
   ```bash
   # 在 Windows 上推荐用 uv 或官方 Python installer
   pip install -r requirements.txt
   ```

### 2.2 VPS 端

```bash
# 确保 rsync 可用
which rsync || sudo apt install rsync
# 确保 SSH user 有项目目录读权限
ls -la ~/project/pokemir/
```

### 2.3 Linux VPS 端：项目 venv

PEP 668 系统下不可全局 `pip install`。建项目 venv：

```bash
cd ~/project/pokemir
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

之后所有 Python 命令走 `.venv/bin/python` 或先 `source .venv/bin/activate`：

```bash
.venv/bin/pytest tests/ -v
.venv/bin/python main.py pipeline   # 注：管道仅在 Win 端有意义
```

> ⚠️ **Python 版本注意**：项目目标 Python 3.13.13（见 `decisions.md` 2026-05-03），但若 VPS 仅有 3.12，3.12 venv 对纯逻辑测试完全够用。Win 测试机仍建议 3.13.13 与目标一致。

### 2.4 Linux VPS 端：数据库（Docker Compose）

为让 `test_storage.py` 能本地跑（当前 3 用例永久 skip），用 Docker Compose 起一个本地 PostgreSQL 15。数据 volume 落项目内 `.docker-data/postgres/`（已 gitignored，R-10 严格合规）。

**授权来源**：
- `requirement-discussions/2026-05-18_03-00-00_Linux dev装postgres与MCP必要性.md`（confirmed，Q1=D）
- Docker 官方文档（context7 拉取 2026-05-18）：`docs/engine/install/ubuntu.md`

#### A. 装 Docker（一次性系统级前置）

> ⚠️ **不要**用 `sudo apt install docker.io docker-compose-plugin`——Ubuntu 自带仓**没有** `docker-compose-plugin` 包（它是 Docker 官方仓专属）。**必走 Docker 官方仓**，下面 7 个 substep。

**A.1 清理可能冲突的旧 Docker 残留**（保险，幂等）：

```bash
sudo apt remove $(dpkg --get-selections docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc 2>/dev/null | cut -f1) 2>&1 | tail -5
```

**A.2 装前置工具**：

```bash
sudo apt update
sudo apt install -y ca-certificates curl
```

**A.3 装 Docker 官方 GPG key**：

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

**A.4 加 Docker 官方 apt 仓到 sources**：

> ⚠️ **heredoc 顶格警告**：下面命令中**结束的 `EOF` 一定要在第 0 列**（无前导空格/tab），否则 bash 永远不识别结束标记会卡死等输入。把整段拷贝到终端时确保所有行都顶格。

```bash
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
ARCH=$(dpkg --print-architecture)
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $CODENAME
Components: stable
Architectures: $ARCH
Signed-By: /etc/apt/keyrings/docker.asc
EOF
```

验证写入成功：

```bash
cat /etc/apt/sources.list.d/docker.sources    # 应见 6 行；Suites=noble, Architectures=amd64
```

**A.5 刷新 apt 索引 + 装 Docker 全套**：

```bash
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

**A.6 加自己到 docker 组（免 sudo 跑 docker）**：

```bash
sudo usermod -aG docker $USER
newgrp docker
```

⚠️ **安全注意**：加 docker 组 ≈ 给一个无密码 root（docker 能挂载 `/` 进容器读写）。单用户 VPS 可接受。

**A.7 验证**：

```bash
docker --version                # 应见 Docker version 29.x 或更新
docker compose version          # 应见 Docker Compose version v2.x（注意 `compose` 是子命令，无连字符）
docker run --rm hello-world     # 应拉小镜像跑通
```

#### B. 故障排查（A 步常见坑）

| 现象 | 可能原因 | 处理 |
|:---|:---|:---|
| A.4 命令"无反应"（终端停在 `>` 提示符） | heredoc 结束的 `EOF` 前有空格/tab | Ctrl+C 退出，重新粘贴时确保 `EOF` 顶格 |
| A.5 报 "Unable to locate package docker-ce" | A.4 没成功，docker.sources 文件错或为空 | 重看 `cat /etc/apt/sources.list.d/docker.sources` |
| `download.docker.com` 整个不可达（国内网络） | DNS 污染 / 墙 | 把 A.3 和 A.4 中的 URL 改为 `https://mirrors.tencent.com/docker-ce/linux/ubuntu` 或 `https://mirrors.aliyun.com/docker-ce/linux/ubuntu` |
| A.7 `docker run` 报 "permission denied on docker.sock" | A.6 的 `newgrp docker` 没生效 | 退出 SSH 会话重登 |
| `docker compose version` 报 "compose is not a docker command" | A.5 未装上 `docker-compose-plugin` | 重跑 `sudo apt install -y docker-compose-plugin` |

#### C. PG 配置步骤（A 完成后）

1. **设密码**到 `.env`：
   ```bash
   cd /home/alxe/project/pokemir
   # 用 openssl 随机生成 32 字符密码（推荐）
   echo "POKEMIR_DB_PASSWORD=$(openssl rand -hex 16)" >> .env
   chmod 600 .env
   ```
   ⚠️ Linux dev 上的 DB 密码与 Win 端生产 DB **应当不同**（两库本独立）。

2. **启动 PG 容器**：
   ```bash
   docker compose up -d
   docker compose ps   # 等到 STATUS = healthy（约 10-20 秒）
   ```

3. **同步 `.env` 的 DSN**（密码与 step 1 一致）：
   ```bash
   # 编辑 .env，让 POKEMIR_DB_DSN / POKEMIR_DB_DSN_SYNC 使用刚生成的密码
   POKEMIR_DB_DSN=postgresql+asyncpg://poker_user:<step1 密码>@localhost:5432/poker_assistant
   POKEMIR_DB_DSN_SYNC=postgresql://poker_user:<step1 密码>@localhost:5432/poker_assistant
   ```

4. **装 psycopg2-binary**（同步驱动；已加入 `requirements.txt`）：
   ```bash
   .venv/bin/pip install -r requirements.txt
   ```

5. **验证 storage 测试**：
   ```bash
   .venv/bin/pytest tests/test_storage.py -v
   ```
   预期：**3 passed**（之前是 3 skipped）。

6. **装 postgres MCP**（让我能 SELECT 验证 DB）：
   ```bash
   POKEMIR_PG_DSN="postgresql://poker_user:<step1 密码>@localhost:5432/poker_assistant" \
     bash tools/setup-mcps.sh
   # 重启 Claude Code session 后我能 mcp__postgres__query
   ```

#### 日常操作

| 操作 | 命令 |
|:---|:---|
| 启动 | `docker compose up -d` |
| 停止（保留数据） | `docker compose stop` |
| 关闭容器（保留数据） | `docker compose down` |
| 完全清空（含数据） | `docker compose down -v`  或  `rm -rf .docker-data/postgres/` |
| 看日志 | `docker compose logs -f postgres` |
| 进容器交互 SQL | `docker compose exec postgres psql -U poker_user poker_assistant` |

#### 与 Win 端的关系

| 维度 | Linux dev | Win 测试机 |
|:---|:---|:---|
| PG 实例 | 本机 Docker | 本机原生（独立装）|
| 数据 | dev 测试数据 | 真实手牌数据 |
| 密码 | 各自独立 | 各自独立 |
| 同步关系 | **不同步**——两库物理独立，符合 dev/prod 隔离原则 | —— |

> **Win 端 PG 部署不在本节范围**。本节 A/B/C 仅适用 Linux VPS。Win 测试机的 PG 部署有三条可选路径（Docker Desktop / Win 原生安装包 / WSL2），各自的取舍（资源占用、稳定性、与 pipeline 进程的网络拓扑）需要单独 REQ 讨论后再写入。**等用户首次需要在 Win 端跑 `pytest tests/test_storage.py` 或开启生产数据落库时再触发该讨论**。当前 Win 端 pipeline 跑识别+落库**默认假设用户已自行配妥 PG**（与本仓的代码改动解耦）。

#### 安全注意

- **端口绑定**：`docker-compose.yml` 显式绑 `127.0.0.1:5432`，不暴露公网。VPS 防火墙仍建议关 5432 入站
- **`.env` 权限**：`chmod 600 .env`，只有自己可读
- **R-10 合规**：数据 volume `.docker-data/postgres/`（项目内 + gitignored）；Docker daemon 是 system tool（R-10 例外条款）
- **R-2 合规**：`docker-compose.yml` 中 `POSTGRES_PASSWORD: ${POKEMIR_DB_PASSWORD:?...}` 引用 env var，不硬编码

---

## 3. 日常迭代循环

### 3.1 Linux VPS：改代码

- 改完代码直接在 Linux 上跑纯逻辑测试（不涉屏幕/PG 的部分）：
  ```bash
  .venv/bin/pytest tests/test_recognition.py -v  # action parser 等
  ```
- 阶段性进度才 git commit + push（不要为"每改一行同步一次"碎片化 commit；rsync 解决高频同步）

### 3.2 Windows：拉取最新

打开 Git Bash 在 `C:/pokemir-test`：

```bash
bash tools/sync-from-vps.sh
```

或如果 Linux 端已 push 到 origin：

```bash
git pull
```

两条路径**功能等价**。rsync 用于"我刚改完想立刻试"的快速循环；git pull 用于"阶段进度已 push"的语义同步。

### 3.3 Windows：跑验证

```bash
pytest tests/ -v              # 完整测试套件
python main.py pipeline       # 端到端管道（需 poker 客户端窗口）
```

---

## 4. ⚠️ Windows 副本只读约定

**Windows 端 `C:\pokemir-test\` 严禁编辑代码。**

### 理由

- rsync 是单向覆盖（VPS → Win），Win 端任何改动会在下次 sync 时被覆盖丢失
- 已编辑的文件如果未及时同步回 Linux，工作丢失

### 约束

- Win 端**不装** VS Code / PyCharm 等编辑器（只装 Python 运行环境 + Git Bash）
- Win 端**不**做 `git commit` / `git push`
- 若临时改动必须（debug print、试验性代码）：在 Win 端验证完成后，**手动把改动 copy 回 Linux**，在 Linux 端正式 commit；下次 rsync 自动覆盖 Win 端临时改动

### 例外：`tests/fixtures/` 双向流转

fixture 截图是在 Win 端录制的（因为 Linux 看不到 poker 客户端），但需要 commit 回 Linux 共享。流程：

1. Win 端录制新 fixture → 暂存 `tests/fixtures/_pending/`
2. SCP 或直接 SSH copy 到 Linux 端 `tests/fixtures/`
3. Linux 端 git commit + push fixture
4. 下次 rsync 把 fixture 同步回 Win

> 详细 fixture workflow 待 Task #2 落地，本节先占位。

---

## 5. Windows API 调用范围隔离（项目约束）

> 本节内容来自 REQ `requirement-discussions/2026-05-17_21-00-00_项目红线清单.md §阶段 7.4 不入项`——经讨论决定不立为硬红线（因属架构纪律而非外部不可逆损失），改写至本工作流文档。

### 约束

`ctypes.windll` 及 Windows 平台特定 API 调用**仅允许**在以下两个目录的 Python 模块中：

- `capture/`
- `tools/`

其他目录禁止直接调用 Windows API：

- `pipeline/`
- `recognition/`
- `events/`
- `storage/`
- `stats/`
- `hud/`
- `api/`

具体禁止的语句模式：

```python
# ❌ 禁止
import ctypes
from ctypes import windll
ctypes.WinDLL("user32")
ctypes.windll.user32.SomeApi(...)
```

### 理由

- 项目目标平台是 Windows，但**开发主力在 Linux VPS**
- 平台特定逻辑全部下沉到 `capture/` 模块，让 Linux 端能跑 90% 的单元测试
- `pipeline/` / `recognition/` 等核心模块跨平台 → 测试覆盖率高
- 一旦 windll 调用扩散到 `pipeline/`，Linux 端 import 都会失败 → 整个开发循环瘫痪

### 当前合规状态（基线）

- `capture/screen.py` —— 白名单内 ✅（使用 `EnumWindows` / `GetWindowRect` / `GetWindowText` 等只读窗口查询）
- `tools/roi_config.py` —— 白名单内 ✅
- 其他模块 —— 未触发 ✅（已扫描）

### 偏离处理

新代码若必须调用 Windows API：

1. **优先**：封装到 `capture/` 或 `tools/`，对外暴露 platform-neutral 接口（参考 `ScreenCapturer.find_window_by_title` 模式）
2. **不行**：进入 REQ 模式讨论，评估是否升 R-X 红线占新槽位

---

## 6. 环境隔离（R-10 项目工件隔离合规）

为遵守红线 R-10「项目工件隔离」，所有依赖缓存 / 模型权重 / 临时文件应**尽量保留在项目内**，避免污染 `~/` 或其他项目。

### 6.1 推荐环境变量

在项目 `.envrc`（配合 `direnv`）或每次激活 venv 后手动 export：

```bash
# HuggingFace 模型缓存（默认 ~/.cache/huggingface）
export HF_HOME="$(pwd)/.cache/huggingface"

# pip 下载缓存（默认 ~/.cache/pip）
export PIP_CACHE_DIR="$(pwd)/.cache/pip"

# Python __pycache__ 集中位置（避免散落项目子目录）
export PYTHONPYCACHEPREFIX="$(pwd)/.cache/pyc"

# npm cache（默认 ~/.npm）—— 装 MCP servers 时影响
export NPM_CONFIG_CACHE="$(pwd)/.cache/npm"
```

`.cache/` 已在 `.gitignore` 中——不会污染 git。

### 6.2 R-10 命令禁忌

以下命令在 pokemir 任务中**禁止使用**（触发 R-10）：

- `pipx install <pkg>` ← AI 已踩过坑，故立此红线
- `pip install --user <pkg>`
- `pip install -g <pkg>`
- `npm install -g <pkg>` / `pnpm add -g`
- `cargo install <pkg>` 不带 `--root` 限制
- `go install ...` 默认 GOPATH

**替代**：
- Python：`.venv/bin/pip install <pkg>`
- Node：`npm install <pkg>`（无 `-g`，写 `node_modules/`）

### 6.3 已知违例清理（一次性）

| 路径 | 来源 | 清理命令 |
|:---|:---|:---|
| `~/.local/bin/pytest` + `~/.local/share/pipx/venvs/pytest/` | Task #1 任务中 pipx 安装 | `pipx uninstall pytest` |

清理后，pytest 走 `.venv/bin/pytest`（venv 已含 pytest 9.0.3）。

### 6.4 例外（不触发 R-10）

- 系统级工具本身（`rsync` / `git` / `claude` CLI / `apt` / `brew`）
- `claude mcp add` 写入 `~/.claude.json`（Claude Code 自身配置，类似 `git config`）

---

## 7. 故障排查

| 现象 | 可能原因 | 处理 |
|:---|:---|:---|
| `ssh vps-host` 拒绝 | SSH key 未加入 VPS authorized_keys | 重新追加；或确认 SSH key path 一致 |
| `bash tools/sync-from-vps.sh` 报 "missing env vars" | `~/.bashrc` 未配置或未生效 | 检查 `echo $POKEMIR_VPS_HOST`；重开 Git Bash |
| rsync 首次特别慢 | 全量同步 + .git 较大 | 用初始 `git clone` 代替；之后 rsync 增量很快 |
| Win 端代码与 Linux 不一致 | Win 端被编辑过 | 跑 rsync 强制覆盖；改动需从 Linux 找回 |
| pytest 全 skip | 缺 PostgreSQL 或屏幕 | 装本地 PG / 在带屏幕会话跑；详见 `tests/test_storage.py` / `tests/test_capture.py` 顶部说明 |
| `python main.py pipeline` 找不到 poker 窗口 | ROI profile 配错 window_title | `python tools/roi_config.py --name <profile>` 重新框选 |
| `test_capture.py` ImportError numpy | 项目依赖未装在当前 Python | `pip install -r requirements.txt` |

---

## 8. 相关文件

| 文件 | 作用 |
|:---|:---|
| `tools/sync-from-vps.sh` | Windows 端 rsync 同步脚本 |
| `docker-compose.yml` | Linux dev 本地 PG 容器配置（§2.4 / R-10 合规） |
| `.env.example` | 环境变量模板（POKEMIR_DB_DSN / POKEMIR_DB_PASSWORD 等） |
| `requirements.txt` | Python 依赖清单（含 psycopg2-binary 同步驱动） |
| `.agents/project-constraints.md` | 项目红线清单（R-1~R-10） |
