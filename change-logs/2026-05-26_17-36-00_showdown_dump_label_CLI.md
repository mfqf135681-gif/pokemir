# 摊牌训练数据收割管道:dump + 标注 CLI(Tier 1)

- **完成时间**:2026-05-26 17:36
- **关联讨论**:本会话 — 用户问"标本兼治怎么搞",决议分 Tier 1(快路径:让你能录+标)/ Tier 2-3(治根:增强/分域 val/温度校准/NONCARD 类)
- **关联红线**:无新增红线触发
- **后续 commit 接力**:#3-#6(增强 / 分域 val / 温度校准 / NONCARD)将在你录制时 Linux 端并行做
- **关联前次 change-log**:`change-logs/2026-05-26_06-06-00_diagnostic_events_诊断日志基建.md`

## 1. 任务概述

**问题**:上轮 diag 数据揭示 — CNN 在摊牌区识别 rank 普遍 conf 0.45-0.89(suit 满分),0.9 阈值卡掉几乎全部。**根因是训练数据不含摊牌区,CNN 域外泛化差**。

**方案 Tier 1**(本 commit):**收割训练数据的零成本管道**
- pipeline 在每次摊牌候选时,**把候选 seat 的 L/R 牌图自动 dump 到 data/showdown_dumps/**(无论 CNN conf 是否过)
- 配 CLI `tools/label_showdown.py` — cv2 显示牌图,**键盘党 30 秒/张**就能标完 250 张

**为何先 Tier 1**:你 Win 端**今天就能开录** → Linux 端我**并行**做 Tier 2-3 → 录完时 Tier 2-3 也到位 → 增训一气呵成,**不卡你**。

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | `data/` 已 gitignored,dump 不污染仓库 | `.gitignore` 验证 |
| 2 | 每手摊牌产 5-10 张牌图;录 50 手 ≈ 250-500 张,够小幅增训 | 工程经验 |
| 3 | 标注 CLI 用 cv2.imshow 在 Win 桌面环境可工作 | cv2 已在依赖中 |
| 4 | 标注后的 fixture 走 `tests/fixtures/showdown/<card>/` 与现有 `cards/` 分目录,**避免污染原 val 集**(Tier 2 分域 val 的前提)| Tier 4 设计 |
| 5 | CLI 通过 sibling `.labeled` 标记 idempotent — 重跑只续未标 | 工程经验 |

## 3. 文件变更清单

| 文件 | 变更 |
|---|---|
| `pipeline/orchestrator.py` | + `SHOWDOWN_DUMP_ENABLED` 模块常量(`POKEMIR_SHOWDOWN_DUMP=0` 可关);+ `_dump_showdown_crop()` 方法(写 PNG + sibling JSON);`_capture_showdown_cards` 在 CNN 调用之后无条件 dump L/R 两半 |
| `tools/label_showdown.py` ✨**新建** | CLI:walk dumps → cv2 显示(4x 放大)→ 显 CNN 预测+conf → Enter 接受 / `<text>` 改 / s skip / d noncard / q quit → 输出 `tests/fixtures/showdown/<card>/`(或 `showdown_noncard/`) + `.labeled` 标记 |

## 4. 验证

- ✅ Linux 端 inline smoke:3 个 fake dump → CLI 模拟 Enter/Qh/d → 输出 9s/Qh/NONCARD 三路径正确,markers 写入
- ⚠️ pytest 未在 Linux 端跑(VPS 算力弱前次 stop 了)— **依赖 Win 端 `pytest -q` 验证 229 仍 pass**
- ✅ `cards_area` 切左右半图后 dump 路径与原 CNN 调用并行,**不影响 conf gate / history gate 现有逻辑**

## 5. 用户后续操作

1. **Win 端 git pull**
2. **重启 pipeline 录摊牌**(默认 dump 开):`.venv\Scripts\python.exe main.py pipeline --profile party_poker_8`
3. **每手摊牌产 5-10 张 PNG → 累到 ~250 张时**(约录 50 手)
4. **标注**:`.venv\Scripts\python.exe tools/label_showdown.py`
   - cv2 窗口弹出 → 看牌图 → CNN 对就 Enter / 错就敲真值(`9s` 等)/ 不是牌就 `d`
5. **完事跟我说** → 我用你已经标好 + 我并行写完的训练 / 校准代码增训 CNN

## 6. 风险

- ❌ **磁盘占用**:每张 PNG ~5KB,250 张 = 1.25MB / 500 张 = 2.5MB → 不会爆
- ❌ **dump 阻塞主循环**:`_dump_showdown_crop` 整体 try/except,任何 IO 失败 silent;~1ms 写 PNG 在每秒 1 次摊牌频率下可忽略
- ⚠️ **混入垃圾标记**:dump 包括 CNN 假阳(非摊牌但 candidate 通过)— **正是 #6 NONCARD 训练样本**,**不是 bug 是 feature**

## 7. 不在 scope(留给 #3-#6)

- ❌ 数据增强 pipeline → #3
- ❌ 分域 val 集追踪 → #4
- ❌ 温度校准 → #5
- ❌ NONCARD 反幻觉类 → #6

## 关联记忆

- [[recognition-stack-production-ready]](2026-05-24 accepted,本次将被刷新)
- [[cross-validation-architecture-pending]] · [[long-term-roadmap]] #LR2(PaddleOCR 升级,本次不冲突)
