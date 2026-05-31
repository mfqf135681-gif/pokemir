# T102:Hotfix — SeatROI.amount → amount_area attribute typo

- **完成时间**:2026-05-31 09:00 UTC(北京 17:00)
- **关联需求**:Win 端 Sprint 1 verify 第 1 次 run 发现 AttributeError 每 tick crash
- **触发红线**:R-9 测试盲点(Linux MagicMock 没 cover 真 dataclass attr 名)

## 1. 任务概述

**Bug**:`_capture_focus_seat_ocr` 用 `seat.amount`(不存在),应为 `seat.amount_area`(SeatROI dataclass 正确字段名).

**症状**:Win 端 ATTENTION_MODE=1 每 tick 抛 `AttributeError: 'SeatROI' object has no attribute 'amount'`.行为:
- _attention_focus_results 永空(整个 OCR-2 path crash)
- pipeline tick crash 重复 every 250ms
- tick stats 仍跑(虽然报错)
- legacy OCR-1 path 不受影响,基础功能继续

**Linux 单测没抓**:之前测用 `MagicMock`,mock 自动创建任意 attribute → `seat.amount` 看似存在 → 没 raise AttributeError.

## 2. 假设清单

1. `SeatROI` 字段名是 `amount_area`(non-optional 那个版本 → 现实是 Optional)
2. `stack_area` 字段名正确(已 verify)
3. Win 端 ATTENTION_MODE=0 之前 verify 通过(没动 OCR-2 path,T80/T82 layer 无影响)

## 3. 文件变更清单

| 文件 | 变更 | 行数 |
|:---|:---|:---:|
| `pipeline/orchestrator.py` | `_capture_focus_seat_ocr`:`seat.amount` → `seat.amount_area` (× 2 locations) + `stack_area` 显式访问(原 getattr defensive) | +6/-7 |
| `tests/test_orchestrator_ocr_wire.py` | 加 `test_capture_focus_seat_ocr_uses_real_SeatROI_attrs` regression test(用真 SeatROI dataclass) | +24 |
| `change-logs/.../T102_*.md` | 本文件 | - |

**总计 +30/-7 = +23 净行**

## 4. 契约一致性检查

- ✅ 不动 schema / DB
- ✅ Mode=0 行为 100% 不变(本 bug 仅 mode=1 触发)
- ✅ Mode=1 修复后 OCR-2 path 真工作
- ✅ 23/23 tests pass(orchestrator_ocr_wire 全集)
- ✅ Regression test 用真 SeatROI dataclass(不再 MagicMock-hidden bug)

## 5. 红线合规动作

**R-9 测试盲点警示**:
- Linux MagicMock 测试自动创建任意 attribute → **无法 cover dataclass attr 名 typo**
- 必须配合**用真 dataclass instance** 的 regression test(per [[dev-rule-validate-blind-spots]] (a) 红线)
- 本 hotfix 加 `test_capture_focus_seat_ocr_uses_real_SeatROI_attrs` 是经验固化

## 6. 测试结果

```
$ python -m pytest tests/test_orchestrator_ocr_wire.py
============================== 23 passed in 1.38s ==============================

新 regression test:
test_capture_focus_seat_ocr_uses_real_SeatROI_attrs PASSED
(构造真 SeatROI(amount_area=None) → 之前会 raise AttributeError 'amount',
 修复后正常返 {"action_text": "", "amount_text": "", "chip_text": ""})
```

## 7. 手动操作提醒(Win 端)

**立即 hotfix Win**:
```powershell
git pull   # 拿 T102 hotfix
$env:POKEMIR_ATTENTION_MODE = "1"
python main.py pipeline --profile party_poker_8 --observer
# 预期:不再 every-tick AttributeError;OCR-2 真工作
```

## 8. 潜在影响范围

- ✅ Mode=0 行为 0 变化
- 🟢 Mode=1 修复后真工作(之前 OCR-2 path 整段 crash 等于没启用)
- 🟡 Sprint 1 verify 需 Win 重跑(原录失效 — OCR-2 crash 表面 silent 数据无效)

## 9. 违规标注

无违规。**但暴露 R-9 测试方法论 gap**(MagicMock 隐藏 attr typo):
- 经验固化:Phase 1.5 v3.2 后续 sub-step 涉及 dataclass 字段访问时,**必须用真 dataclass instance 测**,不能仅 MagicMock

---

## 任务完成自检 checklist

- ✅ change-log 已写
- ✅ Hotfix 立即可部署
- ✅ Regression test 防 future MagicMock-hidden typo
- ✅ §11 模式守卫(Win 端 bug → DEV 修)
- ✅ §11.3 加法陷阱自检:**纯修 typo,无双轨**
