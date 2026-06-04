# 下注区(amount)识别 + 跨录像迁移 — 验证收口

## 1. 概述

数字识别配方从 stack 推广到第 2 种 ROI **amount(下注额)**,并验**跨录像迁移**。
结论分两半:**stack 模板免费跨录像(~100%);amount 密度依赖(稀87%→厚pool94%)**。
详见记忆 [[digit-ocr-stack-recipe]](已更新)。延续 [[constraint-solver-paradigm]] §15。

## 2. 关键结果

- **amount × 段1**:12 真下注全对(含 s1 零模板纯 pool 跨座读对)。下注区比 stack 多需两件:
  - **icon-prefix 分边采集**:筹码图标成一格,采集按座丢(s0-4 在左丢首格 / s5-7 在右丢尾格);读取端图标自然判 `?` 丢、不分边。
  - **多样本匹配**(`_match_char_multi`):pool 留各座样本,小字粗体 5/6 才分得开。
- **跨录像**:
  - **stack 段1→段2 = 100%**(10帧)→ 免费迁移(模板密)。
  - **amount 段1(稀)→段2 = 87%**(4真错/31);**段1+段2 厚pool→留出170343 = 94%**(1真错/16,小n)。残余=小字圆弧互混(3/8、9/0、6/5、6/8),落在该座无自身样本的数字。→ **amount 要采厚**。
- **时序中位**:救瞬态遮挡(离群),救不了系统性 per-值错(同手逐帧同错)。

## 3. 文件变更

- `tools/digit_probe.py`:新增 `--read-field`(跨区读)、`--harvest-session`(跨录像)、`--harvest-src`(多源密化)、`--icon-prefix`/`--icon-right-seats`(分边图标)、`_match_char_multi`(多样本)、`--scan`(扫真下注去重)、`--verify`/`--verify-out`(交互核对弹图+自动产真值)、`--check`(已核真值diff零重验)。
- `tools/truth_amount_121925.txt`、`verified_amount_130651.txt`、`verified_amount_170343.txt`:三录像 amount 已核真值(交互核对产出)。
- `tools/run_amount_170343.cmd`:长命令脚本(治跨终端粘贴空格)。

## 4. 工程教训(沉淀)

- **跨终端粘长串必被塞空格/换行**:真值进文件(`--harvest-file`)、长命令进 `.cmd` 脚本;结果文件 push→我 pull 读(零粘贴零转录)。
- **人/工具双幻觉**:用户手标 stack 3641/4151 读错、工具对——验证法=工具先读人核对、不一致双查。见 [[feedback-data-driven-mandate]]。
- **停止 LLM 盲调/眼读像素**:改 `--diagnose` 客观数字 + 留出裁决。见 [[dev-rule-validate-blind-spots]]。

## 5. 已知风险

**公告横幅遮挡反复砸 s4 amount**(段2/170343 均 s4、各~3.5s 连续帧)→ s4 amount ROI 在横幅滚动路径上,稳定可复现。归时序/遮挡检测兜底,不归 per-帧 reader。

## 6. 未验 / 下一步

6 种数字区只验 2 种(stack+amount);**剩 4 种**(实时底池粗/上街池粗小/+xx前导+/胜率尾%)未碰(见 #229)。1 种桌皮;live 时序融合 / 接求解器未做。
