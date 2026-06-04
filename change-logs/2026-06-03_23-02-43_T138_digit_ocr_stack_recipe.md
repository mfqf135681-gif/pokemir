# T138:轻量数字识别配方定型 — stack 区验证 ~100%(无 EasyOCR/无 CNN/无 per-seat)

## 1. 任务概述

Option2 数字识别落地验证。目标:轻量、强、native "+"/"%",替 EasyOCR 拾取筹码数字。
结论:**pool 模板 + normalize + width-cap 切割** 三件套,在 **stack 区 × 段1录像** 真·留出 +
跨座验证 **~100%**;**per-seat 模板被证不需要**(normalize 抹平跨座差)。详见记忆 [[digit-ocr-stack-recipe]]。

## 2. 配方与根因(逐步逼近,客观裁决)

- **类1 切割(width-cap)**:`4021/455` raw 切对段数,但 `min_gap=2` 把 "4" 右侧 1px **真字缝**误并
  成 24px 怪格。修:`segment_cells` 加 `max_merge_w=14` —— 仅合并后宽≤14 才并(字内细缝≤14 照并 /
  字间真缝并出~24 不并)。**Linux 单测 +2 全过=接地非盲**。
- **类2/3 归一(normalize)**:发暗帧(折叠态 UI 暗)漏墨 + 5↔6 相关势均(0.85/0.84)。修:每 crop
  min-max 拉满量程再 th/匹配。**一个全局操作连消三害**(发暗 / 5-6 / 跨座渲染差)。**转默认**(`--no-normalize` 关)。
- **匹配**:灰度去均值归一相关,**pool 一套模板跨座通吃**。

## 3. 文件变更清单

- `pipeline/digit_ocr.py`:`segment_cells` 加 `max_merge_w`(宽度上限合并);self-test +2(字间1px真缝不并/字内细缝照并)。
- `tools/digit_probe.py`:`--normalize`(转默认 via `--no-normalize`)、`--max-merge-w`、`--harvest-file`、
  `--harvest-assist N`、`--diagnose`(客观数字诊断,不靠眼读)、`--bootstrap`、`--validate N --seed`、
  `--mode-window`;模板从 pooled→per-seat→回归 pooled(归一后跨座可行)。
- `tools/truth_digit_121925.txt`:段1 stack 真值(seat6/7;含修正 3641/4151)。

## 4. 验证结果(诚实,严限范围)

- **PASS**:stack 区 × 段1(20260603_121925),~22 帧 × 8 座 ≈ 150+ 读数,用户翻图核对全对,
  含 `--validate` 真·留出(随机非harvest帧)+ 跨座(seat0-5 用 pool 模板,从未训练)。
- **时序中位被证无效**:坏值 per帧 0/21(同手逐帧像素同→系统性同错,中位只确认错读)→ 必须每帧读对。
- **⚠️ 未验/不可外推**:① 只 stack(6 种数字区里最简单的 1 种);**另 5 种(下注黄字+图标/底池粗/上街粗小/+xx/胜率%)全没碰**;② 1 录像/1 桌皮;③ 小额/all-in%/+ 标记未专测。

## 5. 红线合规

- [[dev-rule-validate-blind-spots]]:切割逻辑抽纯核 + Linux 单测;cv2 部分 Win 实测;**停止盲调匹配器**(连推两版退步后),改 `--diagnose` 客观数字诊断,**不用 LLM 眼读像素**(碰 explanation-only 红线 + 我视觉同样幻觉)。
- [[feedback-data-driven-mandate]]:**人眼真值也会幻觉**实证(用户手标 3641→3631、4151→3151)→ 验证法改"工具先读→人核对→不一致双查"。
- image-only ✅(纯截图+模板);LLM 未入数据通路;数据边界 N/A(只读)。

## 6. 下一步

挑**最难的下注区(黄字+筹码图标前缀)**试 `--field bet`,炸了说明配方不通用、需按 ROI 适配;通了再跨录像。
