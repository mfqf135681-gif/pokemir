# pokemir — WePoker 牌局观察 / 学习助手

> 自用工具。对着屏幕上的 WePoker(德州扑克)牌桌**截图 → 识别 → 重建一手牌 → 累积对手画像**,
> 用来复盘、学习、研究对手。**只读屏幕**,不碰游戏内存 / DOM / 网络包(见红线 R-1)。

这份 README 是给**新接手的人或智能体**的单一入口:读完应能完整掌握"这是什么、怎么跑、代码在哪、当前到哪了"。
深入设计看 `AGENTS.md`(治理)和 `requirement-discussions/2026-06-01_95pct-constraint-solver-paradigm.md`(§15 核心架构)。

---

## 1. 一句话定位 + 为什么难

- **目标**:从纯像素逆向出结构化牌谱(谁、在哪条街、下了多少),量化指标=**筹码动作捕获率 95%**。
- **为什么被逼上硬路**:成熟工具(PokerTracker 等)都读牌室导出的**牌谱文件**;WePoker **不导出**,加上"只读屏幕"红线 → 只能截图 OCR。这是这个项目所有复杂度的根源。
- **当前主线范式(§15)**:**别"读"数字,要"解"数字**——单帧 OCR 准率 <95%,但一手打到河牌是**超定**的(德州规则 + 筹码守恒),所以"读准 + 规则解 + 守恒校验"合起来能逼近 95%。详见 §5。

---

## 2. 三机拓扑(先搞清楚"在哪台机器跑什么")

| 节点 | 角色 | 我(Claude)可达 | 算力 | 路径 / Shell / Python |
|---|---|:---:|---|---|
| **Linux VPS** `puzz-virvm` | 代码 dev、docker PostgreSQL、cron 备份 | ✅ 直接 | 弱(重模型 OOM) | `/home/alxe/project/pokemir`、bash、3.12 |
| **Win 主测试机** `Adminiatrator` | **生产 runtime**、截屏、GPU 识别、重模型 | ❌(禁 Remote SSH) | 强(RTX 5070 Ti 16GB) | `D:\project\pokemir`、PowerShell、3.14 |
| **Win2 家用** | dashboard-only | 部分 | — | PowerShell |

**关键含义**(决定一切跨机操作):
- **代码单向流**:Linux 写 → `git push` → GitHub → Win `git pull`(或 `tools/sync-from-vps.sh` rsync)。**永不反向**。
- **识别只能在 Win 跑**(cv2 / EasyOCR / CNN / GPU / 真截屏都是 Linux 没有的)。Linux 只能跑**纯逻辑 + 单测 + DB**。
- **数据回 Linux**:Win pipeline 通过 **Tailscale 私网**(`100.77.23.17 → 100.101.105.46:5432`)写 Linux 的 postgres。结果要给 Claude 看 → **写进 DB,Claude 直接读**,别人肉粘贴控制台。
- PostgreSQL 在 Linux docker(`pokemir-pg`),公网不可达,只听 `127.0.0.1` + Tailscale IP。

---

## 3. 怎么跑(三条主线)

```bash
# ── A. 起 DB(Linux,一次性)──────────────────────────────
docker compose up -d            # 需先在 .env 设 POKEMIR_DB_PASSWORD

# ── B. 实时管线(Win,边打边识别边写库)── 这是"旧管线",1549 手就这么来的
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker_8
#   --observer  观战模式(你没坐下,所有座走对手摊牌捕获)

# ── C. dashboard(看画像/复盘)──────────────────────────
streamlit run dashboard.py      # http://localhost:8501
```

**离线验证新架构(§15)的两步**(Win 录帧 → 回放):
```powershell
# 1) 录一段牌局帧(开 WePoker 坐下后)
.\.venv\Scripts\python.exe tools\record_frames.py --window-title "WePoker" --fps 10 --duration 300
#    → 存 data\recordings\<时间戳>\  (manifest.jsonl + frames\),开录前会问 sb/bb/ante 写进 manifest

# 2) 回放过新 reconstruct+solver,出整手守恒率(对比旧管线 21.3% 基准),写库
.\.venv\Scripts\python.exe tools\replay_reconstruct.py --session data\recordings\<时间戳> ^
    --profile party_poker_8 --solve --conservation --write-db --run-label "run1"
#    → 写 replay_conservation 表,Claude 用 mcp__postgres__query 直接读
```

---

## 4. 架构总览 + 数据流

两条数据通路并存:**(I) 旧实时管线(已在生产,EasyOCR 增量推断)** 与 **(II) §15 新桩基(离线验证中,stack-centric 重建)**。

```
                              ┌─────────────────── 屏幕(WePoker 窗口)
                              │  capture/ (mss 截屏 + ROIManager 按 rois/*.json 裁 ROI)
                              ▼
        ┌─────────────────────────────────────────────────────────┐
        │  recognition/   OCREngine(EasyOCR) · CardRecognizer(CNN→VLM→启发式) · ActionRecognizer │
        └─────────────────────────────────────────────────────────┘
                              │
   ┌──────────────────────────┴──────────────────────────┐
   │ (I) 旧实时管线 [生产中]            │ (II) §15 新桩基 [离线验证,未接 live]      │
   │ pipeline/orchestrator.py          │ pipeline/reconstruct.py (stack序列→动作)    │
   │  _tick(): 逐帧增量推断动作          │ pipeline/solver.py     (规则裁幻影)         │
   │  events/normalizer.py 推断+置信度   │ pipeline/digit_ocr.py  (模板数字识别)       │
   │  events/diag.py 诊断遥测            │ pipeline/conservation.py(守恒判级)          │
   │         │                          │ 由 tools/replay_reconstruct.py 离线驱动      │
   └─────────┼──────────────────────────┴───────────────────────────┘
             ▼
   events/models.py (Hand / ActionEvent 领域对象)
             ▼
   storage/repository.py → storage/database.py (SQLAlchemy)
             ▼
   ┌──────────── PostgreSQL (Linux docker, Tailscale) ────────────┐
   │ hands · action_events · diagnostic_events · 40+ ring-beam 视图 │
   └──────────────────────────────────────────────────────────────┘
             ▼
   dashboard/ (Streamlit 复盘/画像)  ·  stats/ (玩家统计)  ·  api/+hud/ (Phase 4-5 占位)
```

**一手牌生命周期**(orchestrator,旧管线):
`_start_new_hand`(建 Hand、读 hero 牌/按钮/盲注/玩家ID/初始 stack、注入 POST 盲注)
→ 多次 `_tick`(读公共牌/底池/各座动作+stack,增量 emit ActionEvent)
→ `_end_current_hand`(读终局 stack、推保险、CNN 读摊牌牌、finalize 写库)。

---

## 5. 核心概念(读懂这 6 点就懂了一半)

### 5.1 §15 约束求解器范式(当前主线)
- **三档识别器**(难度/可靠度分级):
  - **A 档(CNN 分类)**:动作词 / 牌型 / 按钮 → 已 100% 先例,**易**。
  - **B 档(数字识别)**:stack / 下注额 / 底池 / 赢额 → **最弱、错最狠**,是攻坚点。
  - **C 档(重 OCR)**:玩家 ID → 低频(每座首见读一次)。
- **底池 = 守恒锚**:`底池 ≥ Σ输家投入`、`底池 ≥ 赢家净得` 这类物理不等式做校验(~92%)。**stack 不作守恒锚**(赢家投入隐于净额,单读 stack 守恒只过 21%)。
- **"解"而非"读"**:到河牌超定 ~20 个事件 → 可靠抓 ~15 + 守恒 + 规则补剩 5 → 整套逼近 95%。

### 5.2 数字识别配方(B 档,`pipeline/digit_ocr.py` + `tools/digit_probe.py`)
**无 EasyOCR、无 CNN、无训练**:pool 多样本模板 + `normalize`(p2-p98 拉量程) + 宽度上限切格 + 多样本相关匹配。
实测:stack/底池跨录像 ~100%,amount 厚 pool ~94%。**这是已验证的配方,但还没接进生产管线**。

### 5.3 守恒判级(`pipeline/conservation.py`,本 session 新建)
逐字复刻 DB 视图 `v_hand_conservation` 的判级公式:`chip_movement = Σ初始stack − Σ最终stack`,
`OK` 当 `-10 ≤ cm ≤ pot×0.10+30`,否则 `CHECK_REQUIRED`。**已对全量 1538 手验证与视图 100% 同口径**。
`tools/replay_reconstruct.py --conservation` 用它产出新桩基 vs 旧基准的整手守恒对比。
⚠️ 守恒只查"整手对不对得平",**不等于逐动作捕获率**(后者要 `--truth` 真值)。

### 5.4 card_marker 活跃集(`tools/active_set.py`)
头像左下两张红牌背的 phash,对每座持久参考做 hamming → 判"这一刻谁还在手"。
用来补 reconstruct 看不见的动作(P-6 末位跟注)。同座可靠,跨座/跨侧不可靠。

### 5.5 玩家 ID(C 档)
**OCR 名为主 key + 多帧众数**(灭漂移)+ `find_player_aliases` 模糊合并 + phash **仅同座**确认相似名。
phash 当独立 key 不行(跨侧名字渲染镜像,hamming 跨人/同人重叠)。

### 5.6 治理协议(`AGENTS.md`,智能体接手必读)
- **4 模式**:REQ(需求讨论→`requirement-discussions/`)、DEV(开发→`change-logs/`)、TEST(诊断→`test-reports/`)、DOC(文档)。
- **10 红线**(R-1~R-10):R-1 只读屏幕不碰内存/DOM/包、R-2 禁硬编码凭据、R-3 数据只落自控机器(禁 SaaS)、R-4/R-5 API/数据模型改动须改 `contracts/`、R-9 只 ROI 抓取禁全屏、R-10 禁全局安装…
- **Router 自检**:每次回复显式走"意图分类 → 加载规则 → 红线核验 → 任务自检",不许静默绕过。
- 还有几条本项目沉淀的工作纪律(数据驱动:每个数字必 ground、1 sample 标"不可外推";LLM 仅用于解释层不碰运维/数据)。

---

## 6. 目录地图

| 目录 / 文件 | 作用 | 关键内容 |
|---|---|---|
| **`pipeline/`** | 核心管线(4942 行) | `orchestrator.py`(实时主循环,~2800 行) · `reconstruct.py`(§15 砖1:stack序列→动作) · `solver.py`(砖2:裁幻影) · `digit_ocr.py`(模板数字识别核) · `conservation.py`(守恒判级) · `detector.py`(状态机/手边界) · `state/`(SeatLifecycle/HandPhase,**未接入**) · `io/`(异步 DB/diag 队列,**未接入**) |
| **`recognition/`** | 识别层(Win) | `ocr.py`(OCREngine,EasyOCR 包装,gpu/allowlist/batch) · `cards.py`(CardRecognizer:CNN→SmolVLM→色彩+OCR 三级) · `cnn_classifier.py`(自训 CNN,rank/suit/iscard 三头) · `vision.py`(SmolVLM-256M 备用) · `actions.py`(动作文本解析) |
| **`capture/`** | 截屏 + ROI | `screen.py`(ScreenCapturer,mss) · `roi.py`(ROIManager/TableROIs/SeatROI,读 JSON) |
| **`rois/`** | ROI 配置 | `party_poker_8.json` / `_9.json`(每座 stack/action/amount/fold_area/id/cards/timer/win_amount/button_indicator/card_marker;表级 pot_size)。8座=hero坐下,9座=观战(hero卡 null)。**左右座严格镜像(轴 x≈727)→ 各 ROI 由 card_marker 锚 + 单左模板镜像派生**(`tools/roi_derive.py`,列座 11 字段尺寸全统一;已验证派生 vs 生产守恒读数逐手等价、零回归)。仅中柱座 s0/s4 几个框需手框 |
| **`models/`** | 模型权重 | `card_cnn.pth`(自训,~1MB) · `easyocr/`(~120MB,craft+中英) |
| **`events/`** | 领域模型 + 推断 | `models.py`(ActionType/Street/Position 枚举,Hand/ActionEvent 数据类) · `normalizer.py`(`infer_action_from_delta`/`compute_confidence` 从 stack/pot delta 推动作+物理矛盾兜底) · `diag.py`(诊断遥测) |
| **`storage/`** | ORM + 仓储 | `models.py`(SQLAlchemy 表) · `database.py`(engine/session,读 `DB_DSN_SYNC`) · `repository.py`(Hand/ActionEvent CRUD) |
| **`dashboard/`** | Streamlit UI | `db.py`/`stats.py` + `pages/`(replay/profile/labeling/settings 已实现;coach/live 占位)。view 缺失自动降级 |
| **`stats/`** | 玩家统计 | preflop/postflop/situational(VPIP/PFR/AF…,多为 Phase 3 占位) |
| **`api/` `hud/`** | 服务/覆盖层 | FastAPI `/health` 骨架 + HUD 占位(Phase 4-5) |
| **`tools/`** | CLI 工具集(25 脚本,6697 行) | 见 §7 |
| **`contracts/`** | 契约层(红线 R-4/R-5) | `models.sql`(表结构) · `api.yaml`(接口) · `invariants.md`(牌局不变量,draft,6 待决) · `views.sql` |
| **`tests/`** | pytest + fixtures | `fixtures/cards*`/`showdown/`(CNN 训练数据) |
| **`requirement-discussions/`** | 设计讨论(按主题聚合) | **`2026-06-01_95pct-constraint-solver-paradigm.md` = §15 主线 canonical** |
| **`change-logs/`** | 每次 DEV 的审计日志 | 命名 `YYYY-MM-DD_*.md`,启动前必 grep 同主题 |
| **`docs/`** | 操作手册 | dev-workflow / mcp-setup / dashboard 部署 |
| **`.agents/`** | 治理规则文件 | `rules-*.md`(REQ/DEV/TEST/DOC)+ 项目特化约束/术语 |
| **根级** | — | `main.py`(入口:api/pipeline) · `config.py`(env 配置中枢) · `docker-compose.yml`(PG) · `AGENTS.md`(治理) · `decisions.md`(技术选型,部分已过时如 Python 版本) |

---

## 7. tools/ 速查(按用途 + 平台)

| 组 | 脚本 | 作用 | 平台 |
|---|---|---|:---:|
| 录制 | **`record_frames.py`** | 录 WePoker 窗口为帧+manifest(回放/标注基础) | Win |
| | `record_card.py` | 交互采卡牌 fixture | Win |
| 回放/验证 | **`replay_reconstruct.py`**★ | §15 离线回放:帧→reconstruct+solver→守恒/捕获率(多模式:`--dump-stacks/-pot/-actions/-signals/-active`、`--solve`、`--conservation --write-db`、`--truth`、`--p6`) | Win(`--mock` Linux) |
| | `replay_review.py` | DB 拉低置信事件人工纠偏 | Linux |
| 数字/识别探针 | **`digit_probe.py`**★ | 数字识别配方工具(采模板/自检/`--verify`核对/`--check`跨录像验/`--diagnose`) | Win |
| | `bench_ocr.py`/`bench_cnn.py` | OCR/CNN 吞吐 benchmark | Win |
| | `diagnose_recognition.py` | 卡牌识别诊断 | 任意 |
| ROI/几何 | `roi_config.py`(框选 + `--verify [--element X] [--frame 帧]` 逐元素叠帧核)/`roi_geom.py`(坐标回映 + 镜像/偏移原语)/**`roi_derive.py`**(参数化派生:card_marker 锚 + 模板 + 镜像 → 消手框抖动、立可迁移几何模型;默认 `--dry-run`,`--write` 出 `_derived.json` 不覆盖生产) | 配 / 派生 ROI | Win 框选 / Linux 派生 |
| 标注/训练 | `train_card_cnn.py`/`label_showdown.py`/`label_baseline.py`/`shrink_cards.py`/`capture_empty_seat_baseline.py` | CNN 数据+训练 | Win / 任意 |
| 玩家 ID | `id_phash.py`(phash 验证)/`find_player_aliases.py`(别名合并) | ID | Win / Linux |
| 活跃集 | `active_set.py` | phash→在手区间(库,被 replay 消费) | Linux |
| 运维 | `db_truncate.py`/`health_check.py`/`compare_truth.py`/`frames_to_video.py`/`setup-mcps.sh`/`sync-from-vps.sh` | DB/同步/MCP | Linux / Win |

`run_*.cmd`、`truth_*.txt`、`verified_*.txt` 是前期迭代的伴生快照,非长期工具。

---

## 8. 数据模型(PostgreSQL,~1549 手在库)

| 表 | 作用 | 关键列 |
|---|---|---|
| **`hands`** | 一手元数据 | `id`(UUID)、`hero_cards`/`community_cards`/`seats`(JSONB)、`pot_size_final`、`started_at`、`raw_data`(含 `player_stacks_initial/final`) |
| **`action_events`** | 每个动作一条 | `hand_id`、`player_name`、`position`、`street`、`action_type`(fold/check/call/bet/raise/all_in/post_sb/bb)、`sequence_number`、`amount`、`confidence_score`、`raw_data`(含 `stack_before/after`、`pot_before/after`) |
| **`diagnostic_events`** | 管线决策遥测 | `tag`、`level`、`payload`、`occurred_at` |
| `player_stats_cache` / `player_situational_stats` | 画像缓存 | VPIP/PFR/AF… |
| `event_corrections` / `replay_corrections` | 人工纠偏闭环 | 原值→改值 |
| **`replay_conservation`** | (本 session 新建)新桩基守恒结果 | `run_session`、`run_label`、`hand_index`、`chip_movement`、`pot`、`status` |
| **40+ `v_ring_beam_*` / `v_player_*` 视图** | "圈梁"反推 | 守恒、pot-gap、画像、保险、rake… 如 `v_hand_conservation`、`v_ring_beam_pot_gaps` |

⚠️ 已知:`hands.created_at` 有默认值 bug(全同一时间戳),时间用 `started_at`。

---

## 9. 当前状态(诚实快照,2026-06-04)

| 模块 | 状态 |
|---|---|
| 旧实时管线(orchestrator,EasyOCR 增量推断) | ✅ **生产中**,已采 1549 手 |
| 旧管线质量基准(整手守恒) | OK **21.3%** / CHECK 75% / 逐动作 silent 24.6%(`v_hand_conservation` 实测) |
| §15 reconstruct / solver(砖1/砖2) | ✅ 纯逻辑自测过;⚠️ **未接入 live**(`STACK_PROBE` 是预留钩子,默认关) |
| 切手(hand 分段) | ✅ **2026-06-04 修过切**:旧公共牌假 reset 把 1 手切碎(34 碎片 vs 13 真手)→ 改**多信号交叉印证**(按钮 anchor+≥2、去抖+顺时针单调)。**铁律:报守恒率先确认切手对(手数 vs 按钮移座数);conservation 必要不充分(空 OK 会掩盖切碎)** |
| §15 回放守恒(13 真手,170343) | OK **61%**(切手修正后;碎片版曾虚报 47% 含 12 个"0动作大池"假象) |
| §15 **recall**(真目标 95%) | ⭐ **全 13 手 --truth(用户全标+逐FP视频核对):recall 63/63=100%**(含多人池+all-in)→ **捕获目标本局达标**。precision 42/57,15 FP 归类:真幻影~5(横幅公告盖ROI/全下结算残影)、**全下被胜率%遮挡→街错~6(最大类)**、金额读偏~2(牌型显示盖太快)、真值笔误1+SB1。**守恒 CHECK=读噪声非漏抓,61% 严重低估捕获;瓶颈从来不是 recall**(切手已修+precision读噪声)。⚠️ 仍单 session,跨另 3 段待全标 |
| 数字识别配方(digit_ocr) | ✅ 离线验证(stack~100%/amount~94%);⚠️ **未接入 live**,生产仍用 EasyOCR |
| 守恒对比夹具(conservation + `--write-db`) | ✅ 本 session 建成,与 DB 视图 100% 同口径,闭环(Win 跑→写库→Claude 读) |
| ROI 参数化模型(`roi_derive`,镜像派生) | ✅ **已启用**:统一版(列座 11 字段尺寸全统一+严格镜像)守恒读数与旧版**逐手 bit 等价**(零回归),已替换生产 `party_poker_8.json`。**本质=几何干净/可迁移,非提捕获率**。中柱 s0/s4 离群框(amount/id/fold_area/button)可选重框 |
| 卡牌 CNN | 可用;⚠️ **报识别率看 val 不看 pytest**(pytest 含训练集虚高、val 才是真泛化),摊牌小卡域偏弱 |
| dashboard | 部分(replay/profile/labeling/settings 可用,coach/live 占位) |
| api / hud / stats | Phase 4-5 占位 |

**下一步主线**:把数字配方 + reconstruct/solver 从离线"扶正"进 orchestrator(翻 `STACK_PROBE`),让活管线直接写出新桩基质量;或先 `--truth` 标几手量真实 recall/precision。

---

## 10. 环境与配置(`config.py` + `.env`)

`.env`(gitignored,Win 端需自己配 DB 连接)的关键开关,全部 `POKEMIR_` 前缀:

| env | 默认 | 作用 |
|---|---|---|
| `POKEMIR_DB_DSN_SYNC` | localhost | psycopg2 同步连接串(Win 指向 Tailscale `100.101.105.46:5432`) |
| `POKEMIR_ROI_PROFILE` | party_poker_9 | 用哪套 ROI |
| `POKEMIR_USE_GPU` | 0 | EasyOCR GPU(Win 5070 Ti 需 cu128 wheel) |
| `POKEMIR_OCR_BATCH` / `BATCH_SEAT_OCR` | 0 / 1 | 批处理 OCR 提速 |
| `POKEMIR_ATTENTION_MODE` | 0 | Phase 1.5 双 OCR 实验路径(旧 path fallback) |
| `POKEMIR_STACK_PROBE` | 0 | 逐 tick stack 探针(§15 预留钩子) |
| `POKEMIR_VERBOSE_DIAG` | 0 | 诊断冗长 |

Python:Linux 3.12 / Win 3.14(版本 gap 未对齐,Win 3.14 踩过 cp936 编码坑,全仓已 utf-8 fix)。

---

## 11. 术语表

| 术语 | 含义 |
|---|---|
| **stack-centric / §15** | 以"每座筹码量时间序列"为主信号反推动作的架构范式 |
| **守恒锚 / conservation** | 用底池/筹码守恒等式校验一手是否对得平 |
| **chip_movement** | Σ初始stack − Σ最终stack(应≈底池,扣 rake) |
| **silent (pot-gap)** | 底池动了但没抓到对应动作=漏抓(recall 问题) |
| **card_marker / 活跃集** | 头像角牌背 phash → 判某座是否在手 |
| **ring-beam / 圈梁** | 用德州规则+守恒对高帧事件流做实时求解/过滤/定序的反推层 |
| **三档识别器** | A 分类(易)/ B 数字(难)/ C ID(低频) |
| **profile** | 一套 ROI 配置(`rois/*.json`),对应一种桌型/分辨率 |
| **hero / villain** | 自己 / 对手 |
| **R-1…R-10** | 治理红线(见 AGENTS.md) |

---

## 12. 新智能体导航(从这里继续读)

1. **治理**:`AGENTS.md` — 接手必读,4 模式 + 10 红线 + Router 自检。
2. **核心架构**:`requirement-discussions/2026-06-01_95pct-constraint-solver-paradigm.md` — §15 全貌。
3. **契约**:`contracts/invariants.md`(牌局不变量) + `models.sql`(数据模型)。
4. **最近做了什么**:`change-logs/` 按日期倒序扫最近几篇。
5. **代码起点**:`main.py`(入口)→ `pipeline/orchestrator.py`(实时)/ `pipeline/reconstruct.py`(新架构核)。
6. **数据**:连 `pokemir-pg`,看 `hands` / `action_events` / `v_hand_conservation`。

> 维护约定:本 README 是项目的"地图",**架构/状态有实质变化时更新它**(更新即蒸馏,别 append 流水账)。细节留在各自的 change-log / 设计文档,README 只保持导航准确。
