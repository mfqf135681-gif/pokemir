# T96:Phase 1.5 v3.2 Step 3.1 — OCREngine 多 instance 重构

- **完成时间**:2026-05-31 05:30 UTC(北京 13:30)
- **关联需求**:`requirement-discussions/2026-05-30_phase-1-5-attention-mechanism-design.md` §2.4 Pattern D 双 OCR
- **触发红线**:无

## 1. 任务概述

Step 3 sub-step 3.1 — **OCREngine API instance-specialized 重构**(Linux 可做):

- 加 `name: str = "default"` param(诊断标识)
- 加 `default_allowlist: str = ""` param(instance-level allowlist)
- `name` / `default_allowlist` 暴露为 property
- `read_text` / `read_text_batch` 当 `allowlist=""` 时回退到 `default_allowlist`
- `_init` 加 `logger.info` 标识 instance name
- **向后兼容**:`OCREngine(gpu=True)` 仍 work,name 默认 "default",allowlist 默认 ""

为 Pattern D 双 OCR 准备(Sub-step 3.2 wire 进 orchestrator):
- OCR-1 全局:`OCREngine(gpu=True, name="global", default_allowlist="弃牌跟注让牌加下0123456789")`
- OCR-2 专注:`OCREngine(gpu=True, name="focus")`(动态 per-call allowlist)

## 2. 假设清单

1. EasyOCR Reader 多 instance 可独立 hold state(已 verified — Linux smoke `e1._reader != e2._reader`)
2. 2 instance VRAM ~3 GB(5070 Ti 16 GB 余 13 GB)— Win 端真验
3. Sub-step 3.1 仅 API,不实际加 2 instance 进 orchestrator(Sub-step 3.2 才 wire)

## 3. 文件变更清单

| 文件 | 变更 | 行数 |
|:---|:---|:---:|
| `recognition/ocr.py` | 加 name / default_allowlist param + property + 2 处 allowlist fallback | +24/-3 |
| `tests/test_ocr_multi_instance.py` | 新建 — 6 unit tests(API 验证) | +75 |
| `change-logs/.../T96_*.md` | 本文件 | - |

**总计 +96 行**(纯新增 API,向后兼容)

## 4. 契约一致性检查

- ✅ 不动 schema / orchestrator / pipeline 主路径
- ✅ 现有 `self.ocr = OCREngine(gpu=USE_GPU)`(orch:185)仍 work(name 默认 "default")
- ✅ `read_text(image, allowlist="x")` API 不变
- ✅ 51/51 unit tests pass(44 旧 + 7 新 T96 multi-instance)
- ✅ EasyOCR Reader 实例化路径不变

## 5. 红线合规动作

无触发。Router 4 步:
- ✅ Mode 声明(用户明示 "开发模式 A" 启动 Step 3 → DEV)
- ✅ Step 3.1 = scope 最小(API 重构,不集成)
- ✅ 红线核验(R-1/R-7/R-3 全 NO)
- ✅ 自检:Linux 单测 6 个 cover API 各 facet,Win 端真 OCR 行为待 Step 3.5

## 6. 测试结果

```
$ python -m pytest tests/test_ocr_multi_instance.py tests/test_state_machines.py tests/test_io_queues.py tests/test_detector_state_machine_wire.py
============================== 51 passed in 2.95s ==============================

$ python -c "from recognition.ocr import OCREngine; e1 = OCREngine(gpu=False, name='global', default_allowlist='弃牌'); e2 = OCREngine(gpu=False, name='focus'); print(e1.name, e2.name)"
global focus
```

## 7. 手动操作提醒(Win 端)

**本次无 Win 端动作** — 仅 API 重构,旧调用 `OCREngine(gpu=True)` 行为不变。

下次 Win 端 Step 3.5 真 verify 时:
- 实际 wire 2 instance + Pattern D 协作
- 录 30-60 min 看 tick / silent / VRAM

## 8. 潜在影响范围

- ✅ pipeline 运行时 **行为 0 变化**(只是 API 多了可选 param)
- 🟡 后续 sub-step:
  - 3.2 wire OCR-2 instance 进 orchestrator(ATTENTION_MODE gated)
  - 3.3 Pattern D tick-aligned merge logic
  - 3.4 Ring beam 仲裁 integration
  - 3.5 Win 端实测 verify

## 9. 违规标注

无违规。

---

## 任务完成自检 checklist

- ✅ change-log 已写
- ✅ Linux 6 T96 unit tests + 44 旧 = 51/51 pass
- ✅ §1.6 校准:Step 3.1 API only,不集成 orchestrator
- ✅ §11 模式守卫(用户明示 "开发模式 A" → DEV)
- ✅ §11.3 加法陷阱自检:**纯 API 扩展,向后兼容**,不构成双轨
