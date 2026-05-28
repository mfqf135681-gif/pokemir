# T17 SB/BB blind_level + T18 Step 1 空位 baseline 工具

- **完成时间**:2026-05-28 08:05 UTC(北京 16:05)
- **关联需求讨论**:无(快速实施)
- **关联前次 change-log**:T19 + alias 合并(同一 DEV session 连续推进)
- **触发红线**:无
- **无关红线已检查**:R-1 ~ R-10

## 1. 任务概述

### T17 完整(本会话完成)

- `_detect_blind_levels` 加在 `_start_new_hand → _detect_button_position` 之后
- SB seat = (button+1) % num_seats / BB seat = (button+2) % num_seats
- 抓 amount_area + OCR 数字 + sanity range(0-100000)
- 落 `hand.raw_data['blind_level'] = {sb: X, bb: Y}`
- emit `diag.blind.detected`

### T18 Step 1(工具 ready,gate 待 Step 2)

- `tools/capture_empty_seat_baseline.py` 新建
- 用户输入空 seat indexes → 抓 fold_area + cards 的 phash → 落 `rois/empty_seat_baseline_<profile>.json`
- **Step 2 待**:用户 Win 跑工具后,我改 `_capture_player_ids` 加 EMPTY_SEAT gate(下次 commit)

## 2. 假设清单

1. **T17**:T13 button OCR 已稳定 → SB/BB 位置准确;T24 amount_area 已收窄 → OCR 准
2. **T18**:phash 在同一桌空座状态下稳定(WePoker 空座 UI 应一致)

## 3. 文件变更清单

| 文件 | 变更 | 说明 |
|:---|:---|:---|
| `pipeline/orchestrator.py` | 修改(加 1 函数 ~50 行 + 1 调用) | T17 `_detect_blind_levels` |
| `tools/capture_empty_seat_baseline.py` | 新建(~100 行) | T18 Step 1 工具 |

## 4. 契约一致性检查

- T17 落 `raw_data['blind_level']` — JSONB 字段,**不动 schema**
- T18 工具只读 ROI + 写本地 json(rois/empty_seat_baseline_*.json)
- **R-5/R-6 不触发**

## 5. 红线合规动作

无触发。

## 6. 测试结果

- **T17**:python syntax ✅,**未真测**(需 Win 端重启 pipeline 录新数据 → query `SELECT raw_data->'blind_level' FROM hands ORDER BY started_at DESC LIMIT 1`)
- **T18**:python syntax ✅,**未真测**(需 Win 端实际跑 — 涉及屏幕 capture)
- ⚠️ [[dev-rule-validate-blind-spots]]:Linux 无屏幕 + cv2 GUI,Win 端真验证才是 final gate

## 7. 手动操作提醒

```
⚠️ 手动操作:

T17 验证:
1. Win pull + 重启 pipeline
2. 录 1-2 手后 query:
   SELECT id, raw_data->'blind_level' FROM hands ORDER BY started_at DESC LIMIT 3;
3. 应看到 {"sb": X, "bb": Y}

T18 跑工具(可选,Step 2 启动前):
1. Win 端打开 WePoker,**确认哪些座位空着**
2. python tools/capture_empty_seat_baseline.py --profile party_poker_8
3. 输入空 seat 数字(如 0,3,7)
4. 工具生成 rois/empty_seat_baseline_party_poker_8.json
5. 通知我 → 下次 commit 我改 _capture_player_ids 加 gate
```

## 8. 潜在影响范围

- **T17**:hand.raw_data 多一个字段(JSONB 加 key,**完全向后兼容**)
- **T18**:零影响(只新工具,pipeline 不读 baseline json 直到 Step 2 改)
- **dashboard / view**:**可以**新增 BB-unit 标准化(读 raw_data['blind_level'])— 留给后续

## 9. 违规标注

无违规。Router 4 步全走。

---

## 任务完成自检 checklist

- ✅ `change-logs/2026-05-28_08-05-00_T17_blind_levels_plus_T18_step1.md` 已保存
- ✅ T17 完整 + T18 Step 1 ready
- ✅ 触发红线 ID:无
- ✅ §11 模式漂移遵守(用户明示 "开发模式 执行 T17+18" → DEV)
- ✅ §1.6 校准:T17/T18 都 Linux 不能真测,**已在 §6 + §7 明示 Win 端验证 gate**
