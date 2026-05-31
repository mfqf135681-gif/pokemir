# T103:R15 Quick Fix — stack_area 全押后显胜率("%")检测

- **完成时间**:2026-05-31 09:30 UTC(北京 17:30)
- **关联需求**:用户 Win 端实操发现 `requirement-discussions/2026-05-30_phase-1-5-attention-mechanism-design.md` §3 加 R15
- **触发红线**:无

## 1. 任务概述

**用户实操发现**:WePoker UI 中,all-in 玩家的 stack_area(非下注区)显示胜率"78%",不再显示筹码量.

**Pre-existing bug 严重度**:🔴 **历史污染源,非 v3.2 引入**
- 旧 stack OCR allowlist `"0123456789."` 把"%"滤掉 → "78%" → "78" → 入库当 78 chips
- 污染所有 all-in hand 的 stack tracking → pot delta 关系 → ring beam D1 silent inference → 玩家画像
- 影响所有历史数据(T75/T85/T87 等数据都含此污染)

**Fix(本 commit)**:
1. 加 helper `_ocr_stack_chips(img, seat_idx) -> float | None`:
   - allowlist 加 "%":`"0123456789.%"`
   - 检测 "%" → emit `all_in.winprob_detected` diag → 返 None
   - 否则正常 `_extract_amount`
2. 4 个 stack OCR call site 统一走 helper:
   - `_capture_seat_stacks`(snapshot)
   - `_detect_empty_seats`(stack empty 判断)
   - `_process_seat_actions`(主 stack tracking)
   - `_capture_focus_seat_ocr`(OCR-2 chip_text — allowlist 改 `0-9.%`,调用方判别)
3. Design doc 加 R15(原 13 → 14 规则盲点)

## 2. 假设清单

1. EasyOCR allowlist 加 "%" 不破坏数字识别准度
2. "%" 出现 仅由 all-in equity 触发(非 OCR 噪音)— 用户实操 confirm
3. 旧 stack OCR 数据(含"78"当 78 chips 的污染)无 backfill 计划 — historical 数据接受 known polluted

## 3. 文件变更清单

| 文件 | 变更 | 行数 |
|:---|:---|:---:|
| `pipeline/orchestrator.py` | 加 `_ocr_stack_chips` helper + 4 个 call site 改 | +29/-9 |
| `requirement-discussions/2026-05-30_phase-1-5-attention-mechanism-design.md` | §3 加 R15 详描 + Fix + 未来扩展 | +28 |
| `tests/test_orchestrator_ocr_wire.py` | 加 `TestStackPercentDetectT103`(5 detect tests) | +56 |
| `change-logs/.../T103_*.md` | 本文件 | - |

**总计 +113/-9 = +104 净行**

## 4. 契约一致性检查

- ✅ 不动 schema / DB
- ✅ 现有 stack 字段语义不变(只是污染源消除)
- ✅ Normal stack OCR("1500" 等)行为完全不变
- ✅ All-in equity("78%" 等)返 None 是新增行为 — 上游接受 None(原有 _extract_amount 也返 None for 无数字)
- ✅ 28/28 unit tests pass(23 旧 + 5 新 R15 detect)

## 5. 红线合规动作

无触发。Router 4 步:
- ✅ Mode 声明(用户明示 "开发模式 A" → DEV)
- ✅ R15 = 用户实操发现 → 数据驱动 fix,符合 [[dev-rule-validate-blind-spots]] (a)
- ✅ 红线核验(R-1/R-7/R-3 全 NO)
- ✅ 自检:5 个新 detect tests cover normal/percent/empty/allowlist 路径

## 6. 测试结果

```
$ python -m pytest tests/test_orchestrator_ocr_wire.py -v
TestStackPercentDetectT103::test_ocr_stack_chips_normal_returns_amount PASSED
TestStackPercentDetectT103::test_ocr_stack_chips_percent_returns_none PASSED
TestStackPercentDetectT103::test_ocr_stack_chips_percent_with_leading_text PASSED
TestStackPercentDetectT103::test_ocr_stack_chips_empty_returns_none PASSED
TestStackPercentDetectT103::test_ocr_stack_chips_allowlist_includes_percent PASSED
============================== 28 passed in 1.43s ===============================
```

## 7. 手动操作提醒(Win 端)

**Sprint 1 重新 verify 前**:
```powershell
git pull   # 拿 T103 hotfix
$env:POKEMIR_ATTENTION_MODE = "1"
python main.py pipeline --profile party_poker_8 --observer
# 录 30-60 min

# DB query 验:
# 1. silent fold % 改善(应该比之前 50.6% 真低,因为 stack 污染消除)
# 2. all_in.winprob_detected diag emit count(all-in hand 触发次数)
# 3. pattern_d.ocr2_action_fallback / amount_fallback 触发率
# 4. tick attention_focus_ocr phase ~5-15ms
```

## 8. 潜在影响范围

- ✅ pipeline 运行时(mode=0)**行为 0 变化**(stack OCR allowlist 加 % 也不影响 normal stack 识别)
- 🟢 mode=0 + all-in hand:污染消除,stack tracking 真实
- 🟢 mode=1:OCR-2 chip 同样处理
- 🟡 历史数据:已知含污染,不 backfill

## 9. 违规标注

无违规。

---

## 任务完成自检 checklist

- ✅ change-log 已写
- ✅ Linux 5 detect tests + 23 旧 = 28/28 pass
- ✅ §1.6 校准:R15 fix 是 pre-existing bug 治根,Sprint 1 verify 数据更干净
- ✅ §11 模式守卫(用户明示 "开发模式 A" → DEV)
- ✅ §11.3 加法陷阱自检:**helper 集中 stack OCR % 逻辑,4 site 调用 → 不双轨**
