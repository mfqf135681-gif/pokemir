# 摊牌抓帧 root-cause fix:从 hand-end 一次性改 per-tick 实时

- **完成时间**:2026-05-26 21:26
- **关联前次 change-log**:`change-logs/2026-05-26_20-45-00_防过拟合_早停_分布diff工具.md`
- **关联诊断**:用户跑 `tools/diff_showdown_distribution.py` 输出:fixtures mean_R=205.55 vs dumps mean_R=39.94,luminance chi-square=1.21(超 0.2 阈值 6×),RGB euclidean=224.58(超 20 阈值 11×)+ 用户实地查看 dump 目录确认"一张牌都没抓到全是头像"
- **关联红线**:无新增

## 1. 任务概述

**根本原因**:`_capture_showdown_cards` 只在 `_end_current_hand` 一次性触发,而 `_end_current_hand` 触发条件 = 新一手开始(community 重置 / hero 卡变)。此时摊牌 UI **已经消失** → 抓 `cards_area` 得到的是 idle 头像/背景像素,**不是牌**。

**症状链**(全部自洽):
1. dump 目录无真牌图(只有暗色头像)
2. 训练 fixtures 是用户**手动**截图,在真摊牌瞬间抓的,**正常 mean_R≈205**
3. 推理时 cards_area 抓的是过期头像,**mean_R≈40**
4. CNN 在 OOD 输入上崩塌 → 全部 conf < 0.9 → 0 accepted
5. 多 seat 同时报 9c(CNN attractor on near-uniform 暗像素)
6. 训练 val 100% 没问题(纯属"学得很好",输入对的就给对答案)

**修正方案**:摊牌抓帧从"hand-end 一次性"改为"river 期间 per-tick 实时监测",一旦 `fold_area` hash diverged from baseline(overlay 出现)立即抓 + dump + CNN。**模型 + fixtures + 训练 不动**。

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | fold_area hash diverged 是摊牌 overlay 出现的可靠信号 | 现有 Gate 3 已验证 |
| 2 | WePoker 摊牌 UI 持续 ≥ 2 秒,1 Hz throttle 不会漏 | 用户肉眼观察 |
| 3 | 现有 ckpt 输入对了 conf 就高 — 不需重训 | val 100% + diff 数据证明域内能力没问题 |
| 4 | 每 tick(250ms)hash 计算 8 seat × ~5ms = 40ms,不影响主循环 | 工程经验 |
| 5 | 多个 dump 同 seat 同 hand → 训练数据厚 + 第一个 CNN 通过的 win 决定 | 设计选择(选项 C) |

## 3. 文件变更清单

| 文件 | 变更 |
|---|---|
| `pipeline/detector.py` | + `_showdown_captured_this_hand: dict[int, list[str]]` per-hand 已抓结果存储;+ `_showdown_last_cnn_at: dict[int, float]` 每 seat throttle 时间戳;`start_new_hand` 清零这两个 |
| `pipeline/orchestrator.py` | 主 tick loop 加 `_try_capture_showdown_live(rois)` 调用;新方法 `_try_capture_showdown_live`(per-tick:community=5 + ≥2 active + 每 seat fold_area diverged → 抓 + dump + CNN + 累积);`_capture_showdown_cards` 重构为"hand-end 聚合器"(读 tracker 已存结果 + 发 hand-level diag);Gate 常量提取到类级 `_SHOWDOWN_BASELINE_DIVERGE_THRESHOLD` / `_SHOWDOWN_CONF_THRESHOLD` / `_SHOWDOWN_CNN_THROTTLE_SEC` |

## 4. 验证

- ✅ Linux smoke:StateTracker 新字段创建 + start_new_hand 清零正常
- ✅ orchestrator import 无错
- ✅ `_try_capture_showdown_live` + `_capture_showdown_cards` 都存在
- ✅ Gate 常量正确暴露
- ⚠️ pytest 待 Win 验证 229 仍 pass
- ⚠️ **实战验证待**:Win 跑 pipeline 5-10 min,检查 `data/showdown_dumps/<hand>/seat_X_*_*.png` 是否含真牌(肉眼或 diff 工具)

## 5. 用户后续操作

```powershell
cd D:\project\pokemir
git pull
# 1. pytest 验证 229 仍绿
.venv\Scripts\python.exe -m pytest -q

# 2. 跑 pipeline 5-10 min(自定义俱乐部 2-3 hand 摊牌)
.venv\Scripts\python.exe main.py pipeline --profile party_poker_8

# 3. 验证:dump 目录现在应该有真牌图
Get-ChildItem D:\project\pokemir\data\showdown_dumps -Recurse -Filter *.png |
  Sort-Object LastWriteTime -Descending | Select -First 5
# 双击打开,**应该看见牌而不是头像**

# 4. 跑 diff 工具复测(应该从 chi-square=1.21 降到 < 0.2)
.venv\Scripts\python.exe tools\diff_showdown_distribution.py

# 5. SQL 看 showdown.accepted 比例
```

## 6. 风险

- ⚠️ 每 tick hash 检查增加主循环开销(~40ms/tick = 16% / 250ms);若 tick 变慢,关 throttle 提速
- ⚠️ Throttle 1Hz 在摊牌 < 1s 闪退时可能漏抓 — 实际 WePoker ≥ 2s,但万一切平台时需要调
- ⚠️ 同 seat 多次 dump → 磁盘占用涨 3-5×(原 ~5KB × 10 = 50KB/hand → 现 ~5KB × 30 = 150KB/hand) 仍可接受
- ✅ Gate 6a/6b 仍工作,防 attractor 攻击
- ✅ 现有 fixtures + ckpt 无需变动

## 7. 不在 scope

- ❌ 模型重训 — 域差消除后现有 ckpt 应当生效;如果发现还有 5-10% 残留,**那时**再考虑用 pipeline-dumped 样本重训
- ❌ Hash 阈值自适应 — 当前 6 / 64 bits 实测过

## 关联记忆

- [[recognition-stack-production-ready]] — 待 Win 实战验证 accept_rate 后用真数据更新
- [[showdown-domain-expansion-protocol]] — 协议 Step 6 调整:`label_showdown.py` 现在直接消化 pipeline-dumped 真牌,**手动截图退化为可选**
