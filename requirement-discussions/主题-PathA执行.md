# 主题:Path A 执行(MVP 落库主线)

> 最近更新:2026-05-27 重构归并
> 涉及讨论:5 份(归档于 `_archived_pre_restructure/`)
> 当前状态:**Path A 第 4 步阶段 B accepted — seats×30 ROI + raise + amount + action_events 落库;**
> **下一步:数据质量 P0(见 [[主题-数据质量]]) → Path B**

---

## 索引(按时间倒序)

| 时间 | 子主题 | 状态 | 关键决策 | 原文 |
|:---|:---|:---|:---|:---|
| 2026-05-25 | Path A 第 4 步阶段 B:seats×30 ROI | accepted | 方案 α(单 seat 配 5 ROI 逐次)+ 先配 2 个验证 + pot 持久化 | [archive](_archived_pre_restructure/2026-05-25_02-08-00_PathA第4步阶段B_seats×30_ROI配置.md) |
| 2026-05-25 | Path A 第 4 步:实战数据采集 | confirmed | 分阶段(PG+中文 OCR 并行) → 30-50 手落库目标 | [archive](_archived_pre_restructure/2026-05-25_00-45-00_PathA第4步_实战数据采集.md) |
| 2026-05-24 | Path A 第 3 步:Win pipeline 全链 smoke | accepted | 方案 A minimal smoke,5 分钟启动验证 → 5/5 通过 | [archive](_archived_pre_restructure/2026-05-24_20-54-00_PathA第3步_Win端pipeline全链路smoke.md) |
| 2026-05-22 | 项目大致开发路径 | confirmed | Path A(最小可用识别+落库) → B(数据质量) → C(dashboard) → D(LLM 解释) | [archive](_archived_pre_restructure/2026-05-22_19-35-00_项目大致开发路径.md) |
| 2026-05-19 | Win 端 pull 后行动路径选择 | accepted | 方案 A(最小 sanity) → 视情况 B/C | [archive](_archived_pre_restructure/2026-05-19_02-30-00_Win端pull后行动路径选择.md) |

---

## 当前结论

### Path A 各步骤里程碑

| 步骤 | 内容 | 状态 |
|:---|:---|:---|
| Step 1 | 识别栈最小可用(rank/suit) | ✅ accepted 2026-05-22 |
| Step 2 | 摊牌识别 CNN val 100% | ✅ accepted 2026-05-24 |
| Step 3 | Win pipeline 全链 smoke 5/5 | ✅ accepted 2026-05-25 |
| Step 4 阶段 A | PG + 中文 OCR + 5 手落库 + R-3 扩展 + Tailscale | ✅ accepted 2026-05-25 |
| Step 4 阶段 B | seats×30 ROI + raise + amount + action_events | ✅ accepted 2026-05-26 |
| Step 5 | 数据质量 P0(见 [[主题-数据质量]]) | 🔴 进行中(T3 / T4 / T5) |

### 整体路径

```
Path A(最小可用) ✅ → Path B(数据质量+SQL view) 🔴 → Path C(dashboard 完整) 🟡 → Path D(LLM 解释 #LR7) 🟣
```

## 关联记忆

- [[recognition-stack-production-ready]]
- [[path-a-step-3-accepted]] — Win smoke 5/5
- [[path-a-step-4-stage-a-accepted]] — R-3 + Tailscale + 5 手
- [[path-a-step-4-stage-b-accepted]] — seats×30 + amount
- [[cross-validation-architecture-pending]] — Path B 准备(已实施)
