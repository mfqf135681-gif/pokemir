# Pot guard 修:总底池 label 触发新 hand-start

- **完成时间**:2026-05-26 18:04
- **关联前次 change-log**:`change-logs/2026-05-26_17-45-00_cnn_治根_增强_分域val_温度校准_NONCARD.md`
- **关联讨论**:本会话 — 用户发现 "Pot OCR decrease ignored: 94.0 → 32.0" 反复刷屏,根因是 observer mode 下 hand-start 检测漏触发(hero ROI 不变 + community-reset 250 ms 窗口太窄漏帧),用户确认 "总底池" 文本只在新手开始时短暂出现,选定 A 方案
- **关联红线**:无新增

## 1. 任务概述

**问题**:用户日志显示新手开始后,`_process_pot` guard 反复 reject 新手小 pot(94→32),直到新手 pot 涨过 94 才停。

**根因**:hand-start 信号三路全漏 —
1. ❌ hero card ROI 在 observer mode 是浏览器 chrome,永不变 → 不触发
2. ❌ `_hero_cards_present()` 同上
3. ⚠️ `community_just_reset()` 需要 250 ms tick **恰好抓到 community=0 的发牌瞬间** → race lose

**修**:加第 4 路 hand-start 信号 — pot ROI 文字含 "总底池" + 现有 hand 在跑 + 新 pot < peak × 0.5 → 触发 `_end + _start_new_hand`。新 hand 起始的 `start_new_hand` 内部把 `latest_pot_bb = None`,guard 自动放行。

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | "总底池" 文本只在新手开始时短暂出现(整手不显示)| **用户 Win 端肉眼确认** |
| 2 | 三重保险足以防误判(label + active + drop) | 工程设计 |
| 3 | drop 阈值 0.5(新 pot < peak/2)对所有玩法保险 | 经验:cash/MTT 终局 pot 通常 ≥ 4×BB,新手 pot ≤ 1.5×BB |
| 4 | 误触发代价 = 数据收尾错位 1 手(可接受);漏触发代价 = 短期 pot 卡住直到自然涨过(也可接受)| trade-off |

## 3. 文件变更清单

| 文件 | 变更 |
|---|---|
| `pipeline/orchestrator.py` | `_process_pot` 在 OCR 后 / guard 前加 3 条件信号块 → 若全满足:log + `diag.emit("hand_start.via_pot_label", ...)` + 重新 capture hero ROI + `_end_current_hand` + `_start_new_hand` |

## 4. 验证

- ✅ Linux 端 9 case 逻辑测试全过(normal / 无 active / 无 drop / 小 drop / 无 label / 中场 noise / 无 peak / amount=None / 空 text)
- ✅ orchestrator import 无错
- ⚠️ pytest Win 端验证 229 仍 pass 待你跑
- ⚠️ **真实数据验证**:你 Win 端拉新代码 + 录 5-10 手 →
  ```sql
  SELECT * FROM diagnostic_events
   WHERE tag='hand_start.via_pot_label' AND occurred_at > NOW() - INTERVAL '30 minutes';
  ```
  能看到几条 → 信号工作

## 5. 用户后续操作

```powershell
cd D:\project\pokemir
git pull
# 继续录(同时去开自定义俱乐部)
```

录 5-10 分钟后我帮你 SQL 验证 `hand_start.via_pot_label` 触发情况。

## 6. 风险

- ⚠️ 若 "总底池" 实际整手都显示(用户判断错)→ 三重保险中 drop 条件兜底,中场不触发,**最坏情况退化为现状(continued warning spam)**,不会误清数据
- ⚠️ 若 OCR 把 "总底池" 误读(漏字 / 错字)→ 不触发,**退化为现状**

## 7. 不在 scope

- ❌ Guard 阈值自适应(0.9 不变)— 当前修已能解决用户实际看到的问题
- ❌ Pot ROI 切分(label vs 数字)— 当前一坨 OCR 已够用

## 关联记忆

- 无需新增/更新 — 这是 bug 修,非架构变更
