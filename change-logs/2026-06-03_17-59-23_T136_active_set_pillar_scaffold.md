# T136:活跃集基桩(L1)脚手架 — card_marker ROI + phash 对参考 + --dump-active

## 1. 任务概述

建 L1「活跃集」基桩:头像左下两条红牌背(全玩家统一)= 在手标记。对一份标准牌背参考 phash,hamming≤阈值=该座在手 → 喂 invariants P-6(末位跟注=注级,需"翻后仍活跃")/ P-7(活跃集)。用户截图确认(resource/在手·弃牌·摊牌.png):红牌背在手全程在、弃牌被压暗+「弃牌」盖、**摊牌被显牌占据**(故在手区间止于摊牌=下注街内有效)。
**相邻任务**:T135(ROI 放大框选工具,本次用它框 card_marker)· [[主题-契约]](P-6/P-7 不变量消费本基桩)· #226 solver。

## 2. 假设清单

- 红牌背**全玩家统一图案** → **一份标准参考通吃所有座/玩家**(用户确认)。
- card_marker ROI 紧框两红竖条、不带头像 → phash 对参考干净。
- avg_hash 用 cv2(Win);_hamming + 在手区间判定纯函数(Linux 可验)。

## 3. 文件变更清单

- `tools/active_set.py`(**新增,纯无 cv2**):`active_intervals(samples, th, min_run)` 每帧在手布尔→在手区间(滤单帧抖动)+ `active_set_at(t, intervals)`。
- `tests/test_active_set.py`(**新增**):5 例(单段/弃牌切断/单帧噪声/None/某时刻活跃集)。**Linux 5/5 过**。
- `tools/roi_config.py`:`SEAT_ELEMENT_ORDER` + `ELEMENT_HINTS` 加 `card_marker` 元素(可被 --field/--element/--all-seats 框)。
- `tools/replay_reconstruct.py`:`import active_set` + `load_card_marker_rois` + `--dump-active`(+ `--marker-ref-seat/-t`、`--active-th`):一遍收每座 avg_hash → 对标准参考算 hamming → active_intervals → 打每座在手区间 + hamming 分布(看在手 vs 弃/空分不分得开)。

## 4. 契约一致性检查

- 未改 `contracts/` 字段。新增 `card_marker` 是 profile 内 seat 子元素(可选,非 REQUIRED)→ 不破坏现有 profile。`invariants.md`(draft)U-4 已预留"牌角=活跃",本次落实其检测路径。

## 5. 红线合规动作

- **[[dev-rule-validate-blind-spots]]**:cv2/avg_hash 本机无法验 → 在手区间判定抽 `active_set` 纯函数 + 单测(Linux 5/5);phash 计算 + 框选 = Win 试飞(§7)。
- **[[image-only-compliance-constraint]]**:phash/亮度,合规。
- **信息饥饿**:先 grep 确认 `cards`≠在手标记、`_avg_hash/_hamming` 已有再建;靠用户截图(resource/)确认 UI 而非凭印象。
- LLM / 数据边界:N/A。

## 6. 测试结果

- **Linux 可验**:active_set 5/5、roi_geom 4/4(T135)、四文件 py_compile 语法 OK。
- **Linux 不可验(无 cv2)**:--dump-active 的 avg_hash 实际输出、card_marker 框选 → Win 试飞。

## 7. 手动操作提醒(Win)

```
git pull
# ① 框 8 座 card_marker(两红牌背,头像左下;用新放大工具)
.\.venv\Scripts\python.exe tools\roi_config.py --name party_poker_8 --all-seats --element card_marker
# ② 验在手vs弃分不分得开(挑 ref-seat 在 ref-t 明确在手)
.\.venv\Scripts\python.exe tools\replay_reconstruct.py --session "data\recordings\20260602_170343" --start 0 --end 9999 --decimate 5 --dump-active --marker-ref-seat <某在手座> --marker-ref-t <该座在手的秒>
```
**预期**:在手座 hamming 稳定低、弃/空座高(bimodal);在手区间覆盖该座参与的手的下注街。
**可能 failure(未验)**:① 红牌背太小/框松 → phash 不稳;② 标准参考选的帧那座没真在手 → 全座 hamming 乱;③ th=8 不合适(看 min↔max gap 调)。

## 8. 潜在影响范围

- 纯新增信号通路,不影响现有 reconstruct/replay 主路径(--dump-active 是独立终端模式)。

## 9. 提交说明

- **未 push**:cv2 检测路径未验(本机无 cv2)+ card_marker 框选待做 → 按 [[dev-rule-validate-blind-spots]] + 边界契约,本地 commit,push 待授权。
