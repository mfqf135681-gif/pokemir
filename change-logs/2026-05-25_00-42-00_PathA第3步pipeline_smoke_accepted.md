# Path A 第 3 步 accepted: Win pipeline 全链路 smoke 通过

- **完成时间**：2026-05-25 00:42（DOC 收口）
- **关联需求讨论**：`requirement-discussions/2026-05-24_20-54-00_PathA第3步_Win端pipeline全链路smoke.md`（accepted）+ `requirement-discussions/2026-05-22_19-35-00_项目大致开发路径.md`（path A 第 3 步 → accepted）
- **关联前次 change-log**：本会话 6 个连续 DEV change-log,最后是 2026-05-25_00-39 (suppress empty street log + pot change log)
- **触发红线**：无
- **无关红线已检查**：R-1 到 R-10 全无触发

## 1. Path A 第 3 步完整时间线

| 时间 | 事件 | Commit |
|:---|:---|:---|
| 2026-05-24 20:54 | REQ 开,smoke A 探底 | (REQ doc) |
| 2026-05-24 20:59 | 第 1 个崩溃修: main.py 加 --profile + 默认改 party_poker | 051fb8c |
| 2026-05-24 21:12 | 第 2 + 3 个崩溃修: _extract_amount @staticmethod + no-db 自动降级 | 20602a7 |
| 2026-05-25 00:17 | 第 4 + 5 个 bug 修: 空槽过滤 + community-reset 触发新 hand (观战模式) | c420ca8 |
| 2026-05-25 00:26 | Cosmetic 修: 仅 canonical street 触发日志 + roi_config --field 增量模式 | 2635285 |
| 2026-05-25 00:30 (用户)| 用 `--field pot_size` 配 pot ROI + commit | 731bbcb |
| 2026-05-25 00:39 | Cosmetic 修: 空 community street log 抑制 + pot 变化日志 | 94019c2 |
| **2026-05-25 00:42** | **5/5 验收通过 (本 commit DOC 关单)** | this |

## 2. 5 项验收数据

用户实测日志（Win 5070 Ti,WePoker 观战）:

```
Pot: 162.0 (was None)                       ✓ pot OCR 工作
Pot: 270.0 (was 162.0)                      ✓ pot 街切换更新
Street flop: ['6d', 'Kd', '6s']             ✓ 3 张干净识别(空槽过滤)
Community reset → starting new hand          ✓ 跨手循环(observer 模式)
(无 "Street preflop: []" 噪声)               ✓ cosmetic 清理

跨 3 手 pot 完整追踪: 162→270→reset 32→128→214→reset 32
```

## 3. 已知缺口（path A 第 3 步 scope 外）

| 缺口 | 影响 | 后续 |
|:---|:---|:---|
| Hero 显示伪 `['9c','9c']` | observer 模式无 hero;仅 cosmetic 噪声 | 真实坐桌玩时自动解决 |
| Seat actions 不识别 | seats=[] 配置缺 + 中文 OCR 模型未装(`跟注/加注/弃牌` 中文文本) | path A 第 4 步 / B 阶段;独立 sub-REQ |
| No-DB 模式 | 数据不落库 | path A 第 4 步装 PG(独立 REQ 决定 Docker Desktop / 原生 / WSL2) |
| 1-2 张 flop 动画中间帧不报 | 期望行为 | (已修) |

## 4. 文件变更清单（本 DOC commit）

| 文件 | 变更 |
|:---|:---|
| `requirement-discussions/2026-05-24_20-54-00_PathA第3步_...md` | pending → accepted + 落地结果 |
| `requirement-discussions/2026-05-22_19-35-00_项目大致开发路径.md` | 第 3 步标 accepted |
| `change-logs/2026-05-25_00-42-00_PathA第3步pipeline_smoke_accepted.md` | 本文件 |
| `MEMORY.md` + 新记忆 `path-a-step-3-pipeline-smoke-accepted.md` | 下次会话开局自动加载 |

## 5. 红线合规

无触发。整 6 个 DEV commits + 2 user commits 中:
- R-1 / image-only-compliance: 严守(仅 mss 截图,无 DOM / 插件 / 抓包)
- R-3 / data exfiltration: no-db 模式连本地也不写,更安全
- R-7 / ROI 一致性: roi_config --field 增量模式正是为缓解 R-7 footgun 而加
- R-8: 识别模型加载不变(CNN production-ready,见前期 doc)

## 6. 测试结果

- Linux smoke: pytest 14 passed / 3 skipped / 0 failed(非 fixture 套件,通过)
- Win 实测(决定性): 5/5 验收通过

## 7. 手动操作提醒

⚠️ **path A 第 4 步开始前**(下次会话):
1. 用户需安装 PG on Win(选型 REQ)——可用之后 pipeline 数据落库
2. 用户需补 ROI: 6 个座位 × 5 ROI each = 30 个 seat ROIs(用 `--field` 增量模式可单个加)
3. 装 easyocr 中文模型: `easyocr.Reader(['en', 'ch_sim'])` 用于动作文字 OCR
4. 这三项每个都独立 sub-REQ

## 8. 潜在影响范围

- **正向**：
  - Path A 主路径 4 步中 3 步完成（fixture/识别/pipeline smoke）
  - 项目证明在真实 Win 环境可跑;非空中楼阁
  - 多个工具升级遗留:`--field`、`--profile`、`no-db` 模式 都是后续多次使用的基建
- **行为变化**：无运行时变化(本 commit 是 DOC)
- **关联待办**：开 path A 第 4 步 REQ

## 9. 违规标注

无。

---

## 给下次会话的"开局上下文"

下次会话开始时记忆会自动加载这条:

- Path A 完成: 1/2/3(fixture/识别/pipeline)
- Path A 待做: 4(实战数据采集)
- 三个 sub-REQ 待开: Win PG 部署 + seats × 30 ROI + 中文 OCR
- 用户已熬数轮高强度 debug,体力已用,**不要再 path A 第 3 步循环**
