# Path A 第 3 步：Win 端 pipeline 全链路 smoke

- **讨论时间**：2026-05-24 20:54
- **状态**：pending
- **触发红线**：本讨论不触发；下游执行会触发 R-1（ToS）/ R-3（数据外传）/ R-7（ROI 完整性）/ R-9（屏幕捕获）
- **无关红线已检查**：R-2, R-4, R-5, R-6, R-8, R-10
- **关联既有讨论**：
  - `requirement-discussions/2026-05-22_19-35-00_项目大致开发路径.md`（confirmed；本次是 path A 第 3 步）
  - `requirement-discussions/2026-05-23_05-12-00_用户识别方案_跨手稳定player_id.md`（accepted；hand-start ID OCR 还没实测）
- **关联记忆**：[[recognition-stack-production-ready]] · [[wepoker-card-brightness-variance]] · [[user-machine-topology]] · [[image-only-compliance-constraint]]

---

## 阶段 1：需求理解与复述

**用户目标**：让 WePoker 牌桌识别 pipeline **在 Win 端真实跑起来一次**,验证从屏幕截图 → 识别 → 状态追踪 → 数据落库的完整链路。

**业务背景**：
- 识别栈已 production-ready (100% val + 100% diagnose,见 [[recognition-stack-production-ready]])
- 但 pipeline runtime 在 Win 端**从未实测**——所有"OK"都是 Linux 上代码 review + 单元测试的间接推论
- 真上 Win 大概率会暴露多层集成 bug,这是 path A 收官前最大的未知区

**核心目标**：先**最小可用 smoke**（pipeline 启动不崩 + 至少 1 手数据落库）→ 出 bug 单子 → 后续迭代修

**模糊点**（阶段 5 待澄清）：
- Win 端 PG 状态（之前 test_storage 全 skip,说明无 PG)
- 实际 ROI 配置完整性（git 显示残缺,但 Win 本地可能更新过）
- WePoker UI 上中文动作文字（"跟注/加注"）的 OCR 可行性

---

## 阶段 2：现状分析

### 2.1 代码侧（Linux dev,git tracked）

| 工件 | 状态 | 说明 |
|:---|:---:|:---|
| `main.py pipeline` 入口 | ✅ 存在 | `PipelineOrchestrator.start()` 进 100ms tick 循环 |
| `pipeline/orchestrator.py` | ✅ 完整 | 含 hand 检测 / community / seat actions / pot 5 子流程 |
| `pipeline/detector.py` StateTracker | ✅ 完整 | hash / 动作 / community / hand 生命周期 |
| `recognition/cards.py` | ✅ 三级链 + CNN production-ready | CNN 模型在 Win `models/card_cnn.pth` |
| `recognition/ocr.py` | ✅ 支持 allowlist | 但**仅装英文模型**;中文 OCR (动作文字 "跟注") 未装 |
| `storage/*.py` | ✅ ORM + repo + init_db | 写入需 PG 可达 |

### 2.2 配置侧（关键 gap 在这里）

```json
当前 git 中 rois/party_poker.json:
{
  "window_title": "WePoker",       // ✅ 修过了
  "hero_card_1": [...],            // ✅ 配过
  "hero_card_2": [...],            // ✅ 配过
  "community_cards": [             // ⚠️ 单条横向 strip,不是 5 个独立 ROI
    [470, 392, 544, 136]
  ],
  "pot_size": null,                // ❌ 未配
  "seats": []                      // ❌ 完全空——影响最大
}
```

**严重 ROI 缺口**:
- **seats × 6 = 全空**：pipeline 无法读 action / stack / button / cards / id_area —— **path A 第 3 步直接死**
- pot_size 未配
- community_cards 是单 strip（fixture 录制阶段可能在 Win 本地配过 5 个,但 git 中是旧版本)

### 2.3 基础设施侧

| 项 | 状态 |
|:---|:---|
| **Win 端 PG** | ❌ 未确认（之前 test_storage 全 skip,推断无 PG）|
| Win 端 venv | ✅ 已装,torch GPU 版 + opencv + easyocr 全在 |
| **EasyOCR 中文模型** | ❌ 未装（当前 `easyocr.Reader(['en'])`）；动作文字 "跟注/加注" 是中文 |
| Win pipeline 实测记录 | ❌ 0 次（这就是本次目标） |

### 2.4 风险地图（按可能炸的概率排）

| 风险点 | 可能性 | 严重性 |
|:---|:---:|:---:|
| seat ROI 全缺,pipeline 启动后什么都识别不到 | 🔴 100% | 阻塞 |
| WePoker 中文动作 OCR 失败 | 🔴 ~80%（无中文模型）| 高 |
| Win 无 PG → pipeline 写入失败 → 全 hand 丢失 | 🟡 100%（除非装 PG）| 高 |
| hand-start 触发逻辑（hero 卡变化）在实战中误判 | 🟡 中 | 中 |
| button 检测启发式（亮度差异）误判 | 🟡 中 | 中 |
| 100ms tick 在 Win 端 CPU 跑不动 | 🟢 低 | 中 |
| Pipeline 在 hand 状态机里 stuck | 🟢 低 | 高 |

---

## 阶段 3：方案设计（3 档 smoke 深度）

### 方案 A — Minimal smoke（推荐起步）

**目标**：pipeline 启动**不崩**,**正确进入主循环**,**不需要识别正确**

**Win 端工作**：
1. 跑 `python main.py pipeline`
2. 观察 5 分钟,看终端日志
3. 不需要 PG（pipeline 会在第一次写入时崩,我们 catch 这个为已知 bug）

**期望**:
- ✅ ScreenCapturer 找到 WePoker 窗口
- ✅ 进 _tick() 主循环
- ✅ 大概率因为 seats=[] 报错或安静地 noop（DB 写入失败可能 catch 住继续）

**Bug 单**: 收集 traceback + 现象,我据此规划下一步

### 方案 B — Functional smoke（中等）

**目标**：识别 + 写入**至少 1 手完整**到 DB

**前置工作**：
1. **Win 端装 PG**（独立 REQ 讨论选型,本次默认走 Docker Desktop 或 PostgreSQL 原生安装包)
2. **配齐 ROI**：6 个 seats × 5 ROIs each + pot_size + 5 individual community = 35 个 ROI 框选
3. **装 EasyOCR 中文模型**：`easyocr.Reader(['en', 'ch_sim'])`

**Win 端工作**：跑 pipeline → 真实牌局 → 至少 1 手成功 hand_id 在 DB

**估时**: 1-2 小时 ROI 配置 + 0.5h PG 装机 + 实测调 bug 1-2 小时 = **3-5 小时**

### 方案 C — Full smoke（深度）

**目标**：N 手数据稳定落库 + 数据完整性验证

**额外**: 1 小时观战 + SQL 抽查 hands/action_events 表数据完整

**估时**: 方案 B 完成基础上 +2 小时

---

## 阶段 4：对比与推荐

| 维度 | A Minimal | **B Functional** | C Full |
|:---|:---:|:---:|:---:|
| Win 用户工作量 | 5 分钟 | 3-5 小时 | 5-7 小时 |
| 暴露 bug 量 | 1-2 个（基础崩溃）| 8-15 个（识别 + DB + ROI）| 15-25 个 |
| 进展信息密度 | 低 | **高** | 高（但边际递减）|
| 风险 | 启动崩了也没数据可分析 | 投入大,但是真正实战测试 | 过度投入 |

### 推荐：**先 A 后 B**（分两轮）

**理由**：
1. **A 5 分钟成本极低**——立刻知道"pipeline 启动到哪一步崩";有针对性后续规划
2. **B 是真实战测试**,但前置工作大(PG + 35 ROI + 中文模型),应该等 A 出 bug 单后再决定怎么走
3. **C 留给后续**——B 跑通后,真数据落库稳定再做

**Plan A 立即可做（仅 5 分钟）**:
- 不动 ROI / 不装 PG / 不装中文模型
- 启动 pipeline 看怎么死
- 收 traceback

### scope 边界

❌ 本 REQ 不决定 Win 端 PG 选型（独立 REQ）
❌ 不写新代码（A smoke 用现有 main.py 即可）
❌ 不补 fixture（识别栈已 100%）

---

## 阶段 5：待你确认

**没意见回 OK 我按推荐走。**

### Q1：先 A 还是直接 B？

🎯 选项：
- **先 A minimal smoke**（推荐）→ 你 5 分钟跑一遍 `python main.py pipeline` 看怎么死,贴 traceback 给我
- 直接 B Functional → 你 3-5 小时前置工作 + 实测;我估算太重,不建议一上来这么干

💡 推荐 **A**。**原因**：你刚力竭,5 分钟试探 vs 3-5 小时大干,**先小投入获取信息再决定下一步**。

### Q2：跑 A 时的窗口约定

🎯 选项：
- **F11 全屏 Chrome,固定 1920×1080（或你屏分辨率）**（推荐）→ 后续 ROI 全在这个窗口状态下配,简单稳
- 不全屏,沿用之前你随便的窗口尺寸

💡 推荐 F11。**原因**：和你刚刚决定的"路径 A: 固定窗口"完全配套;F11 是最简单"固定状态"。

---

## 切换提示

确认后:
- A：你 5 分钟跑 + 报 traceback;我读 → 开 path A 第 3.x 子 REQ 看怎么修
- B（如果选）: 我开一系列 sub-REQ(PG 选型 / ROI 完整配置 / 中文 OCR),你逐项 OK 后展开

---

## 违规标注

无。
