# live 按钮检测改白占比 argmax(step 2a;治 live 卡 seat0)

## 1. 概述

为把"按钮权威切手"(T139,回放验过)搬进 live,**先量 live 按钮稳定性 → 发现检测坏了**:
多人桌 40 分钟 27 手,`button_seat_index` 只出现 0(22)/2(4)/null(1),**从不轮转**。
根因=`_detect_button_position` 的 L1 是 **OCR "D" 第一个命中**,seat0 button_indicator 持久
假 "D" → 永远短路成 seat0。**而夹具/T139 用的是【白占比 argmax,不 OCR】(验过稳)——
验过的方法没上 live**(同本会话 amount 模板坑、第三次同款元 bug)。

## 2. 改动(pipeline/orchestrator.py `_detect_button_position`)

- 删 L1(OCR"D"first-match)+ L2(brightness ratio)+ L3(fallback seat0)三层。
- 换成**白占比 argmax**:每座 button_indicator 算 `(img.min(axis=2) > 170).mean()`,取最白座,
  `best_wf > 0.12` 才认,否则 **button=None**(不再 fallback seat0=T13 假阳根源)。
- **忠实复制** `tools/replay_reconstruct.py:_white_frac(98) + build_series_real(152-158)`
  的 `white_th=170 / frac_th=0.12`——非自创。
- mss=BGRA 4 通道:alpha=255≥white_th 不改 `min(axis=2)` → 与夹具 BGR 三通道同结果。
- diag `button.detected` 候选字段 ocr→white_frac;level 按 method=="white-frac"。

## 3. 安全性(button=None 早有契约)

下游早为 button=None 设计:指针初始化 `if button is not None`(orch:626,注释"button 不可知
pointer 留 None")、blind 检测 skip_reason `button_seat_index_none`(689)、位置 fallback
ValueError 容错(642)。老代码总 fallback seat0 从不产 None;现诚实产 None = 更优(宁空不错座)。

## 4. 验证

- Linux:`py_compile` OK;下游 None 守卫已确认(626/689/2788)。
- ⚠️ **白占比 cv2 路径 Win-only 未验**(盲点):white_th=170 在录像上调的,live 屏幕抓取(mss)
  亮度可能微差。**live 验收标准=重跑后 `button_seat_index` 能随手轮转**(不再卡 0/2)。
- 这是 **step 2a(修检测)**;2b(按钮移动→在线去抖切手 + 观战=按钮权威 + 总底池兜底)
  待检测验收通过后再做——不在未验证信号上盖楼(T139 自己的铁律)。

## 5. 严限

仅 1 桌皮/1 session 观测出的卡死;frac_th=0.12 沿用录像默认,live 可能需微调(看验收)。
