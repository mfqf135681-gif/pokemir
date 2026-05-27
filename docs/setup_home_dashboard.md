# 家里电脑首次 setup — Pokemir dashboard

适用:你回家用低配电脑做 Streamlit dashboard 开发,**不跑 pipeline**,只读 VPS DB.

---

## 前置要求

- **Python 3.12+**(`python --version` 确认)
- **git**(`git --version` 确认)
- **Tailscale 客户端**(下载:https://tailscale.com/download)
- 任何**浏览器**(Chrome / Edge / Firefox 等)

OS:Windows / macOS / Linux 均可。

---

## Step 1 — Clone 仓库

```bash
git clone git@github.com:mfqf135681-gif/pokemir.git
cd pokemir
```

如果用 HTTPS:
```bash
git clone https://github.com/mfqf135681-gif/pokemir.git
cd pokemir
```

---

## Step 2 — Python 虚拟环境

```bash
# Win PowerShell:
python -m venv .venv
.venv\Scripts\activate

# Mac / Linux:
python -m venv .venv
source .venv/bin/activate
```

确认 venv 激活:终端前缀有 `(.venv)`。

---

## Step 3 — 装 dashboard 子集依赖

```bash
pip install -r requirements-dashboard.txt
```

> 💡 国内网速慢时加镜像:
> ```bash
> pip install -r requirements-dashboard.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```

**注意**:不要装 `requirements.txt` — 那是 pipeline 完整依赖,包含 PyTorch / EasyOCR(几 GB,**你这台不需要**)。

预计装包时间:**1-3 分钟**(取决于网速)。

---

## Step 4 — Tailscale 链接 mesh

1. 装 Tailscale 客户端
2. 启动 + 用账号 `mfqf135681@gmail.com` 登录
3. 终端确认在线:
   ```bash
   tailscale status
   ```
   应该看到自己 IP(类似 100.x.x.x)+ 其他设备(包括 VPS 100.101.105.46)
4. ping VPS 验证:
   ```bash
   ping 100.101.105.46
   ```
   应该收到回复。

> ⚠️ 如果首次链 mesh,**确认这台设备已被邀请到你的 Tailnet**(在 Win 桌面这边检查 / 联系我加白名单)。

---

## Step 5 — 配 .env(DB 连接)

```bash
# 复制 example
cp .env.example .env

# 编辑 .env(用记事本 / VS Code / 等):
# 改一行:POKEMIR_DB_DSN_SYNC=postgresql://poker_user:poker_pass@100.101.105.46:5432/poker_assistant
```

实际密码 / user 见你 Win 桌面那边的 `.env`。**密码不在 git 里**,你需要从 Win 桌面 .env 拷贝过来或重新设。

> 💡 也可以**不配 .env** — `dashboard/db.py` 有默认值指向 Tailnet 节点。但需要确认 PG 角色密码跟默认匹配。

---

## Step 6 — 验证 DB 连通

```bash
python -c "from dashboard.db import db_health_check; print(db_health_check())"
```

期望输出:
```
(True, 'N hands')   # 其中 N 是当前累计 hand 数
```

失败常见情况见下方"常见错误"。

---

## Step 7 — 启动 dashboard

```bash
streamlit run dashboard.py
```

浏览器**自动打开** http://localhost:8501,看到 Pokemir 侧栏 + 4 模块。

> 💡 想停止 dashboard:终端 Ctrl+C。

---

## Step 8(可选)— 装桌面图标(Chrome PWA)

1. 在 Chrome 打开 `http://localhost:8501`
2. 地址栏右侧点击 `⊕ Install Pokemir` 图标
3. 桌面 / 开始菜单出现 `Pokemir` 图标
4. 之后双击图标启动(类似桌面 App 体验)

---

## 常见错误

### Q: `pip install psycopg2 failed`

A: 用 `psycopg2-binary`(`requirements-dashboard.txt` 默认已是)。**无需 PostgreSQL dev libs**。

### Q: `Could not connect to host 100.101.105.46`

A: 检查 Tailscale 客户端是否登录 + 在线:
```bash
tailscale status
# 应该看到自己设备 + VPS 100.101.105.46
```
如果没看到 VPS,**联系我**(可能要在 Tailnet ACL 加你设备)。

### Q: `Permission denied for table hands`

A: PG 角色权限不对。检查 `.env` 里的 user/password 跟 VPS PG 的 `poker_user` 角色一致。

### Q: `streamlit: command not found`

A: 检查 venv 是否激活(终端前缀有 `(.venv)`)。如果没激活,运行 Step 2 末尾命令。

### Q: 浏览器开了但页面一直转圈

A: 大概率 DB 连接挂了(查询超时)。在 sidebar 看 `DB: 🔴 ...` 错误信息。先解决 DB 再用。

### Q: Streamlit 启动后页面是英文,我想中文

A: Streamlit 本身英文,但 dashboard 内容已经中文化。如果你看到 "Made with Streamlit" 之类小字,**不可改**(无害)。

### Q: dashboard 显示 "等待 view 实施" 各处

A: 正常 — Path B 的 SQL view 还没在 VPS PG 实施。dashboard 骨架 + Fallback 显示基础表数据兜底。等 view 上线 dashboard 自动 pick up。

### Q: 我改了 dashboard/ 下某个文件,要重启 streamlit 吗?

A: 不用。`.streamlit/config.toml` 已设 `runOnSave = true`,改 `.py` 文件自动 reload。

---

## 开发流程建议

1. **拉 main**:`git pull` 拿最新 backend
2. **编辑**:`dashboard/pages/<module>.py`
3. **看效果**:浏览器自动刷新(runOnSave)
4. **commit + push**:小 commit,描述清晰

冲突场景:Win 桌面那边可能同时改 backend / pipeline。绝大多数 frontend 文件不冲突。如果冲突 `git merge` 解决,**先沟通谁负责哪文件**。

---

## 联系 / 报问题

- DB 查询慢 / 超时
- 某 view 字段名跟代码不一致
- Streamlit 渲染异常
- Tailscale 链不上

→ 直接联系我(Claude),贴**错误信息 + 截图**最快定位。
