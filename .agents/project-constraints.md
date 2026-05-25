# 项目硬红线清单（pokemir / poker 桌面识别系统）

> ⚠️ **本文件由 AGENTS.md 第三步强制核验**。每条红线必须可在任务开始时被显性引用。
>
> 🚨 **总数上限 10 条**。超过 10 条说明项目正在用红线代替架构治理，应做拆分。
>
> 📅 **最近一次复审**：2026-05-17（v0.2.1 落地 + 22:30 重计合并 + 退役 2 条）；2026-05-18（增补 R-10 项目工件隔离）
> 📌 **下次复审节点**：2026-08-17（季度复审）
> 📚 **当前清单授权**：`requirement-discussions/2026-05-17_21-00-00_项目红线清单.md`（status: confirmed，阶段 7 方案 B + 阶段 8 方案 A）

---

## 一、红线索引表

| ID | 一句话标题 | 触发场景 | 违反后果 |
|:---|:---|:---|:---|
| R-1 | 牌室 ToS 合规 | 引入键鼠注入 / 进程内存 / 抓包 / 非白名单 Win hook | 立即停止；REQ 评估 ToS |
| R-2 | 凭据硬编码禁止 | 源码 default 含真实密码/token；或代码内 `password=` / `token=` 字面量 | 立即停止；迁 .env；轮换 |
| R-3 | 数据仅限用户自控基础设施 | 向**第三方运维可读**的服务（公有 SaaS）发送 hands / action_events / * 表数据 | 立即停止；保留至用户自控目标；REQ 讨论脱敏 |
| R-4 | API 契约不可绕过 | 新增/修改对外接口或字段；修改 `contracts/api.yaml` | 阻塞；契约已声明 + 修改须 confirmed 讨论 |
| R-5 | 数据模型契约不可绕过 | 新增/修改数据库表或字段写入；修改 `contracts/models.sql` | 阻塞；契约已声明 + 修改须 confirmed 讨论 |
| R-6 | ORM 与 SQL 模型同步 | 修改 `contracts/models.sql` | 阻塞；同步 `storage/models.py`；必要时生成 Alembic migration |
| R-7 | ROI 配置结构一致性 | 修改 `rois/*` 或 `tools/roi_config.py` 配置结构 | 阻塞；change-log §7 提示重跑 roi_config |
| R-8 | 视觉识别配置一致性 | 修改 `recognition/` 模型加载相关代码 | 阻塞；change-log §7 提示核对 `POKEMIR_MODEL_DIR` / `HF_ENDPOINT` |
| R-9 | 屏幕捕获范围限定 | `pipeline/` 调用 `capture()` / `capture_raw()`；`tools/` 之外模块调用 `capture_raw()` | 阻塞；改为 `capture_roi()` 或 REQ 讨论新 ROI 类型 |
| R-10 | 项目工件隔离 | `pipx install` / `pip install --user` / `npm install -g` 类全局工具安装；代码硬编码项目外写入路径 | 阻塞；改装到 `.venv/` 或 `node_modules/`；清理已污染 |

**当前红线数：10 / 10**（剩余配额 0 条；下次新增前必须先合并/退役一条）

---

## 二、红线详细描述

### R-1：牌室 ToS 合规

**核心约束**：禁止主动与 poker 客户端交互。仅允许"看屏幕 + 查窗口位置"——被动只读。

**触发条件**（任一命中即整条触发）：
- 引入 / 调用键鼠或进程内存类库：`pyautogui` / `keyboard` / `pynput` / `pymem` / 用于 poker 进程的 `psutil.Process.memory_maps`
- 抓包 poker 流量：`scapy` / mitmproxy / 任何 packet sniffer / SSL 中间人
- 调用 Windows hook / 输入注入 API：`SetWindowsHookEx` / `SendInput` / `keybd_event` / `mouse_event` / `BlockInput`
- 白名单内允许调用（不触发）：`EnumWindows` / `GetWindowRect` / `GetWindowText` / `GetWindowTextLength` / `IsWindowVisible`

**为什么是红线**：主流牌室（GG / PokerStars / WPK / 888）反作弊均会检测此类行为。一旦触发账号封禁 + 资金扣留 + 可能法律诉讼。这条边界由牌室定，项目能做的只有"不主动越界"。

**合规动作**：
1. **立即停止** 当前任务
2. 任何相关方向的讨论必须先进入 REQ 模式评估 ToS 风险
3. change-log §5 显式标注 `R-1 触发 + 评估结论 + 已停止`

**例外条款**：无。任何"自动化辅助"提议必须先 REQ 讨论 + 用户明确批准。

---

### R-2：凭据硬编码禁止

**核心约束**：源码不得包含真实密码、token、API key。所有敏感值走 `.env` + `os.getenv()` + 占位符 default。

**触发条件**：
- `os.getenv(KEY, DEFAULT)` 的 `DEFAULT` 值含非占位符密码（占位符识别：`change-me` / `poker_pass` / `your-token-here` / `<placeholder>` 类视为占位符；其他形如 `Lc%40135681` / `sk-xxxxxxxxxxxxxx` 视为真实凭据）
- `requirements.txt` / `.env` / `.env.example` 之外的任何文件出现 `password=Xxx` / `token=Xxx` / `api_key=Xxx` / `secret=Xxx` 字面量（其中 `Xxx` 不是占位符）
- 提交至 git 的文件含 AWS/GCP/Azure key 模式（`AKIA...` / `AIza...` / `sk-...` 等）

**为什么是红线**：git history 永久泄露密码 → 公开仓 / 私仓被破后 DB 被洗。

**合规动作**：
1. **立即停止** 写入操作
2. 真实值迁移至 `.env`（用户本地维护，不入 git）
3. 源码 `default` 改占位符
4. 若 git history 已泄露真实凭据 → 立即在外部系统轮换；不要假设 force-push 能擦除历史
5. change-log §5 记录 `R-2 触发 + 已迁移路径 + 是否需要轮换`

**已知违例（待清理）**：
- `config.py:9, 13` —— 真实 DB 密码 `Lc%40135681` 作为 `DB_DSN` / `DB_DSN_SYNC` 的 default 值
- **后续 DEV 任务必须清理**；建议命名 `清理config.py硬编码凭据`

**例外条款**：无。

---

### R-3：数据仅限用户自控基础设施

**核心约束**：手牌相关表的数据**必须落在用户自控的基础设施**上 — 包括 `localhost` / 用户拥有的 VPS / 用户租赁的私有 mesh（如 Tailscale 接入的私有 IP）。**禁止落到"第三方运维可读"的服务**(公有 SaaS / 托管数据库的运营方可读权限范围)。

> **2026-05-25 修订说明**：原措辞为"本地手牌数据不外传 / 向非 localhost 发送即触发"。经 REQ `2026-05-25_01-14-00_R3边界扩展_私有云数据库架构.md`（confirmed）讨论：用户判定**项目流传失控**风险大于数据外泄风险,需要私有云数据库作为"fork 没凭据无法运行"的反流传闸口。新措辞保留"自控"内核,允许 VPS / Tailscale mesh,排除公有 SaaS。

**判定原则**：
- ✅ **不触发**：localhost / 用户自有 VPS(可经 SSH / Tailscale / WireGuard 等加密私网到达)/ 同 LAN 私有机
- ❌ **触发**：Supabase / Neon / Render / RDS 之类托管 PG(其运营方运维有数据库 root 访问能力)
- ❌ **触发**：任何 HTTP/SDK 上报到第三方分析平台(Mixpanel / Amplitude / Datadog 业务面板)
- ✅ **例外（不触发）**：纯运行时监控(CPU / 内存 / 进程崩溃堆栈,如 Sentry / PostHog 等仅采纯错误流),前提**不读取业务表**

**关注的表**：`hands` · `action_events` · `player_stats_cache` · `player_situational_stats` · `replay_corrections`

**为什么是红线**：
1. 手牌数据是高敏感个人数据 + 可能涉及隐私法规
2. 用户自控基础设施 = 用户控制凭据与访问 → 真正"自己持有"
3. 第三方运维可读 = 用户失去最终控制权

**合规动作**：
1. **立即停止** 触发性的代码或配置
2. 数据保留至用户自控目标(localhost 备用)
3. 如确需走第三方:先 REQ 模式讨论
   - 数据脱敏方案(玩家名 hash / 牌面去标识 / 金额归一化)
   - 用户显式同意机制(首次启动弹窗、可关闭开关、明确的隐私政策链接)
4. change-log §5 记录

**例外条款**：用户在 REQ 中显式同意的上报方案,按 confirmed 记录执行。

---

### R-4：API 契约不可绕过

**触发的判定逻辑**：
- 新增对外接口调用 → 触发，必须在 `contracts/api.yaml` 有定义
- 修改请求体或响应体字段 → 触发
- **修改 `contracts/api.yaml` 本身 → 触发**（合规动作含必须的 confirmed 讨论授权，下方第 3 步）

**为什么是红线**：契约是模块协作的单一事实来源。绕过契约的字段会导致测试与生产行为不一致。

**合规动作**：
1. 先确认 `contracts/api.yaml` 已声明对应接口和字段
2. 未声明 → 停止，进入 REQ 模式讨论契约修订
3. 经 REQ `confirmed` / `accepted` 后修订契约
4. change-log §1 / §4 显式 cross-link 该 discussion 文件路径
5. change-log §5 记录 `R-4 触发 + 引用的 discussion 路径 + 变更摘要`

**例外条款**：
- 纯注释 / 空白 / 排版修改（不改 schema 语义）不触发
- `requirement-discussions/` 中存在 `confirmed` / `accepted` 状态的契约修订记录 → 按记录执行

---

### R-5：数据模型契约不可绕过

**触发的判定逻辑**：
- 新增 / 修改数据库表或字段写入 → 触发
- 新增 / 修改索引 → 触发
- **修改 `contracts/models.sql` 本身 → 触发**（合规动作含必须的 confirmed 讨论授权）

**为什么是红线**：数据模型契约保护数据结构一致性。本项目用 PostgreSQL，schema 漂移会导致 ORM 与实际表结构不符。

**合规动作**：
1. 先确认 `contracts/models.sql` 已声明对应表和字段
2. 未声明 → 停止，进入 REQ 模式讨论契约修订
3. 经 REQ `confirmed` 后修订契约 + 同步 ORM（见 R-6）
4. change-log §1 / §4 显式 cross-link
5. change-log §5 记录

**例外条款**：同 R-4。

---

### R-6：ORM 与 SQL 模型同步

**触发的判定逻辑**：
- 修改 `contracts/models.sql` 任一表或字段 → 触发

**为什么是红线**：SQL schema 与 Python ORM (`storage/models.py`) 必须保持一致。只改一边会导致运行时类型错误或写入失败。

**合规动作**：
1. 编辑 `contracts/models.sql`
2. 同步编辑 `storage/models.py` 对应 ORM 类定义
3. 必要时生成 Alembic migration 脚本
4. 在 change-log §7 标注 "⚠️ 手动操作：执行 alembic upgrade head"

**例外条款**：纯注释修改、字段顺序调整等不改变 schema 语义的改动。

---

### R-7：ROI 配置结构一致性

**触发的判定逻辑**：
- 修改 `rois/` 下任一文件 → 触发
- 修改 `tools/roi_config.py` 的配置结构 → 触发
- 修改 `capture/roi.py` 中 `TableROIs` / `SeatROI` 的字段定义 → 触发

**为什么是红线**：ROI（Region of Interest）配置定义了屏幕上要识别的精确区域。结构变更会让所有已有 profile 失效或错位识别。

**合规动作**：
1. 修改 ROI 结构后，**必须在 change-log §7 提示**：
   ```
   ⚠️ 手动操作：重新运行 python tools/roi_config.py --name <profile> 验证现有 profile
   ```
2. 在 PR/commit 描述中明确告知用户哪些 profile 可能受影响

**例外条款**：纯文档/注释修改。

---

### R-8：视觉识别配置一致性

**触发的判定逻辑**：
- 修改 `recognition/` 下与模型加载相关的代码 → 触发
- 修改 Vision client (`recognition/vision.py`) 初始化逻辑 → 触发
- 修改 `config.py` 中 `POKEMIR_MODEL_DIR` / `HF_ENDPOINT` / `VISION_MODEL` 默认值 → 触发

**为什么是红线**：识别模型路径或 HuggingFace endpoint 配置漂移会导致识别全面失败，且本地与远程环境表现可能不同。

**合规动作**：
1. 修改后必须在 change-log §7 提示：
   ```
   ⚠️ 手动操作：确认 POKEMIR_MODEL_DIR 与 HF_ENDPOINT 环境变量与新配置一致
   ```
2. 若环境变量定义变更，同步更新 `.env.example`

**例外条款**：日志、调试输出等不影响行为的改动。

---

### R-9：屏幕捕获范围限定

**核心约束**：全屏抓取仅限 ROI 配置工具。运行时 pipeline 只能 ROI 抓取，且 ROI 必须基于已声明的 window。

**触发条件**：
- `pipeline/` 下任何模块调用 `ScreenCapturer.capture()` 或 `ScreenCapturer.capture_raw()`（应使用 `capture_roi()`）
- `tools/` 之外的任何模块调用 `capture_raw()`（`capture_raw()` 是 ROI 配置工具的特权 API）
- 新增 ROI 类型未在 `capture/roi.py:TableROIs` 字段中声明就被读取

**为什么是红线**：pipeline 若全屏抓取 → OCR 抓到用户其他窗口（微信、邮件、文档）→ 隐私事故。同时 ROI 类型未声明就读取会破坏配置结构契约。

**合规动作**：
1. **立即停止**
2. 将调用改为 `capture_roi()` + 显式 ROI
3. 若 ROI 类型不足以表达需求 → REQ 讨论新增 ROI 类型（同步触发 R-7）
4. change-log §5 记录

**例外条款**：REQ confirmed 的新 ROI 类型方案，按记录执行。

---

### R-10：项目工件隔离

**核心约束**：本项目创建的所有持久化文件（venv / 缓存 / 模型 / 工具）必须在 `~/project/pokemir/` 项目目录内。禁止"撒"到 `~/`、`/tmp/`（除合理临时）、其他项目目录。

**触发条件**：
- 调用以下全局工具安装命令：
  - `pipx install <pkg>`
  - `pip install --user <pkg>`
  - `pip install -g <pkg>` / `pip install -G <pkg>`
  - `npm install -g <pkg>` / `pnpm add -g <pkg>` / `yarn global add <pkg>`
  - `cargo install <pkg>` 不带 `--root` 限制
  - `go install ...` 默认 `GOPATH`
- 代码中硬编码项目外写入路径（例外：明确的 system path `/etc/` / `/var/log/` 等）
- 创建 venv 在项目目录外

**为什么是红线**：跨项目"污染"导致磁盘占用难溯源、工具版本冲突、清理困难。已发生过实例（见下方「已知违例」）。

**合规动作**：
1. **立即停止** 命令执行
2. 改装到项目本地：
   - Python 包：`.venv/bin/pip install <pkg>`
   - Node 包：`npm install <pkg>`（无 `-g`，写入 `node_modules/`）
   - Rust/Go 工具：按项目要求设 `--root` 或 local `GOPATH`
3. 已污染清理：执行对应的 `uninstall` 命令
4. change-log §5 记录 `R-10 触发 + 替代方案 + 已清理项`

**例外条款**：
- 使用系统级工具本身（`rsync` / `git` / `claude` CLI / 系统 `apt` / `brew`）不在此限——这些是"环境工具"非"项目工件"
- `claude mcp add` 写入 `~/.claude.json` 也不在此限——这是 Claude Code 自身配置，类似 `git config`

### 已知违例（待清理）

- `~/.local/bin/pytest` + `~/.local/share/pipx/venvs/pytest/`
  - 来源：`change-logs/2026-05-17_23-00-00_Task1同步工作流配置.md §3`（pipx 安装）
  - 修复路径：`pipx uninstall pytest`；`.venv/bin/pytest` 已可用替代
  - 清理任务：本次（2026-05-18）R-10 落地任务一并执行

---

## 三、红线收纳标准

新增红线前，**全部满足**才能纳入：

| ✅ 应当成为红线 | ❌ 不应成为红线 |
|:---|:---|
| 触发场景可被 AI 机械判断（路径 / 关键字 / 签名） | 触发条件主观（"代码风格一致"） |
| 违规后果阻塞性（生产事故 / 数据漂移） | 仅是"最佳实践偏好" |
| 无法通过 lint / type-check / 测试自动捕获 | 已有自动化工具能拦截（应优先用工具） |
| 不能通过架构改造在 ≤ 2 周内消解 | 可以快速重构消解（应去消解，不留红线） |
| 跨任务反复出现 | 一次性的 incident（写入 change-log 即可） |
| 是项目级共识 | 个人偏好或临时约定 |

**新增红线流程**：
1. 在 REQ 模式下讨论该红线的必要性、触发条件、合规动作
2. 检查当前红线数：若已 ≥ 10，必须先合并 / 退役一条
3. 用户确认后写入本文件，并在 `change-logs/` 留下引入记录

**退役标准**：
- 长期治理方向已落地（架构改造消解了红线触发条件）
- 连续 3 个月未在任何任务中被触发，且评估认为已不再有风险
- **退役前必须确认无任何活跃 change-log 引用该 ID**（grep `change-logs/` + `test-reports/`）。有引用则不能退役，必须先处理依赖
- 退役需在本文件保留"已退役红线"段，记录 ID + 退役日期 + 原因，避免 ID 复用

---

## 四、已退役红线（保留 ID 不复用）

| ID | 标题 | 退役日期 | 退役原因 | 重启决策需求 |
|:---|:---|:---|:---|:---|
| 旧 R-4 | 测试套件标红即阻塞 | 2026-05-17 | `rules-dev.md §5.2` 已规定相同效果（"所有测试必须通过 / 修复实现代码而非测试"），红线层重复 | 必须先在 REQ 模式讨论，确认 rules-dev §5.2 是否仍覆盖 |
| 旧 R-5 | 历史代码保留 | 2026-05-17 | `rules-dev.md §4 修改规则`已规定"除非用户明确要求，否则禁止删除已有代码"，红线层重复 | 同上 |

**注**：本次重计前的旧 R-1 / R-2 / R-3 / R-6 / R-7 已**改写并重新编号**为新 R-4 / R-5 / R-6 / R-7 / R-8（合规动作扩展含 "修改须 confirmed 讨论"），不视为退役，无需保留 ID 槽位。授权来源：`requirement-discussions/2026-05-17_21-00-00_项目红线清单.md §阶段 7`（confirmed）。
