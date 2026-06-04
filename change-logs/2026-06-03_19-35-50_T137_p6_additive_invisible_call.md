# T137:P-6 加法推理 — 活跃集补看不见的末位跟注(接入 reconstruct)

## 1. 任务概述

把活跃集基桩接进重建,实现 invariants **P-6**:某座本街有捕获动作但 to_amount < 注级、
且【翻后仍活跃】(card_marker 活跃集)、非 all-in → 补一笔"跟到注级"的 inferred call。
治用户洞察的"末位跟注秒进池、人眼难辨"。**相邻**:[[card-marker-active-set-pillar]]、#226 solver、[[主题-契约]] P-6/待决①。

## 2. 假设清单

- 翻后仍活跃(active_intervals 覆盖下街起点)= 该座没弃 → 必跟到了注级(德州规则)。
- 活跃集高置信(card_marker 下界99%+bimodal,已验)→ 可作 P-6 的 conf-gate。
- 保守:只补【有捕获动作】的座(canonical:下注后被加、看不见跟平),不补零捕获(避免盲注当跟注)。

## 3. 文件变更清单

- `pipeline/solver.py`:新增 `infer_p6_calls(actions, active_intervals, street_starts, config)`(纯函数)。三闸:翻后仍活跃 / 非all-in / 注级>0。末街跳过(marker 摊牌被占)。self-test +3(canonical 补、闸1弃牌不补、闸2短all-in不补)→ **Linux 全过**。
- `tools/replay_reconstruct.py`:`build_truth_series` 加 `card_marker_rois` 读 avg_hash 序列(返回 marker_hash);`--truth` 每手循环按 `--p6` 用存档 card_marker_ref 算活跃集 → `infer_p6_calls` → 补动作。`--p6` 开关。

## 4. 契约一致性检查

- 落实 `contracts/invariants.md`(draft)P-6 的检测/纠正路径;`可纠正(强 conf-gate)` 由三闸实现。未改契约字段。

## 5. 红线合规动作

- **[[dev-rule-validate-blind-spots]]**:P-6 核心逻辑 `infer_p6_calls` 抽纯函数 + 单测(Linux 3/3);cv2 活跃集读取 + 真值 recall delta = Win 实测。
- **R3 教训**:保守(只补有动作的座)+ 三闸,防过度加法污染。
- image-only ✅(phash);LLM/数据边界 N/A。

## 6. 测试结果

- **Linux**:solver self-test 含 P-6 3 例全过;active_set 5/5;py_compile OK。
- **Win 待测**:`--p6` 真实活跃集补动作 + 三样本 recall/precision delta。

## 7. 手动操作提醒(Win 验)

```
git pull
# 三样本各跑【带 --p6】对比基线(看 recall 是否上升、precision 是否守住)
.\.venv\Scripts\python.exe tools\replay_reconstruct.py --session "data\recordings\20260602_170343" --start 0 --end 9999 --decimate 5 --truth tools\truth_170343_full.txt --p6
.\.venv\Scripts\python.exe tools\replay_reconstruct.py --session "data\recordings\20260603_121925" --start 0 --end 700  --decimate 5 --truth tools\truth_121925_full.txt --p6
.\.venv\Scripts\python.exe tools\replay_reconstruct.py --session "data\recordings\20260603_130651" --start 0 --end 520  --decimate 5 --truth tools\truth_130651_full.txt --p6
```
**预期**:recall ↑(补回 truth 有、之前漏的末位跟注),precision 不掉。每手 "P-6 补 seatX..." note 可见。
**failure mode(未验)**:① 补多了(precision 掉)= P-6 过度,收紧闸 ② 补的金额/街错位 ③ 活跃集边界致补错街。

## 8. 潜在影响范围

- 仅 `--p6` 开时生效(默认关);加法只 append inferred(标 confidence 0.6),不动既有动作。

## 9. 提交说明

- tools/pipeline/tests/change-log,Win 迭代默认推(便于试飞);P-6 真实效果待 Win recall delta 实测。
