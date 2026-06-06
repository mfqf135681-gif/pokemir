# P3 删死代码:双OCR/attention + shadow_pointer + STACK_PROBE

## 1. 概述

精简计划 P3:删除从未上 live 的实验代码(全 default-off / gated)。**四步法**(深读→评估→修正→执行)
执行,**深读 + grep 兜底各抓到一个会删错的坑**(见 §3),非莽删。

## 2. 删除内容

- **双OCR/attention 整套**(用户判定"蛋疼"、从未上 live):`ATTENTION_MODE` flag、第二 OCR 引擎
  `ocr_focus`、`_attention_focus_results`、`get_focus_seat`、`_capture_focus_seat_ocr`、
  `_pattern_d_merge_action/amount`(+ 它们在 `_process_seat_actions` 的 no-op 调用点)、
  `_observe_multi_pot`(深读发现**从未被调用**)。
- **shadow_pointer**:`_shadow_pointer_scan`(91 行)+ `SHADOW_POINTER` flag + gated 调用。
- **STACK_PROBE**:gated 探针块 + flag。
- 配套:`_TICK_PHASES` 去 `shadow_pointer`/`attention_focus_ocr`/`seat_pattern_d`;sub_ms 同步;
  误导注释("ATTENTION_MODE-gated skip" 实为 live skip)清理;config flag 删除。
- 测试:`test_orchestrator_ocr_wire.py` 删 attention/focus/pattern_d/multi_pot 测试类、仅留 live 的
  `_ocr_stack_chips`;`test_detector_state_machine_wire.py` 的 `is_skippable` 测试去 ATTENTION_MODE 分支。

## 3. 深读/grep 抓到的两个会删错的坑(数据驱动护栏)

1. **`detector.py:is_skippable_seat` 有 LIVE 的 `from config import ATTENTION_MODE; if ATTENTION_MODE:`**
   —— 删 config flag 后这个 import 会**运行时崩**。orchestrator-only 深读漏了,**全仓 grep 兜住** →
   一并删该分支(collapse 到 live 的 `_folded ∪ _empty`)。
2. **`is_skippable_seat` 那 6 处注释** 写"ATTENTION_MODE-gated"但**实际是 live 的 fold/空座跳过**
   —— 深读识别,**只清注释、保留 skip 码**。
3. 另:`_pointer_state`/`_process_timer`/`_finalize_timer` 是 LIVE(UTG 初始化 + 行动期 timer),
   **非** shadow_pointer 的 → 保留;OCR_BATCH 是独立批处理 → 划归 P4,P3 不动。

## 4. 验证

- `py_compile` 全部受影响文件(orchestrator/detector/config/roi/reconstruct/2 测试)OK;
- **全仓 grep:被删符号零 LIVE 引用**(仅余历史注释/docstring,无 import、无调用);
- reconstruct 单测 18 ✅(未受影响);tests 改后 `py_compile` OK(pytest 在 Win 跑,Linux 无)。
- 行为零变化(删的全是 default-off/gated/未调用,live 路径未触碰)。

## 5. 成效 / 严限

- orchestrator 3116→2859 行(P3 净 ~-260),config 精简,砍 ATTENTION_MODE/SHADOW_POINTER/STACK_PROBE 3 flag。
- 仍需 **Win pytest 实跑确认**(Linux 无 pytest,只 py_compile);改的两测试文件 Win 验。
- OCR_BATCH / BATCH_SEAT_OCR 未动(P4);_process_seat_actions 拆分(P5)在此瘦身基座上做。
