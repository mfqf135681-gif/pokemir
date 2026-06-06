# 各数字区独立模板 — 管线 + 采样工具(amount 先行)

## 1. 概述

用户拍板:**每个数字区用自己的模板,不共用**(虽笨但零跨区耦合)。治 cut1
痛点——live 用 stack 模板读 amount 导致 3↔4 误读(stack 大白字 ≠ amount 小粗字)。
本次只动 **Linux 可做的两件**:① orchestrator 支持按区加载多套模板;② 采样工具
`build_digit_templates.py` 支持 amount 图标处理。**真正采 amount 模板 + 量准度是
Win-only**,待 Win 端执行。

## 2. 改动

### pipeline/orchestrator.py — 单 reader → 各区 reader 字典
- `self._digit_reader`(default/stack,仍作"配方是否生效"哨兵)+ 新 `self._zone_readers`
  `{zone: DigitReader}`。init 时扫 `digit_templates_{profile}_{zone}.json`
  (zone ∈ stack/amount/pot/potprev/herobet),逐个 load。
- `default/stack` 优先 `_stack.json`,否则回退**旧单文件** `digit_templates_{profile}.json`
  (向后兼容:Win 现有那份继续生效)。
- 新 `_reader_for(zone)`:取该区 reader,缺则回退 default。
- amount 读路径 `self._digit_reader.read(...)` → `self._reader_for("amount").read(...)`。
- **零退化保证**:无 `_amount.json` 时 amount 仍回退 stack/default = 当前行为。

### tools/build_digit_templates.py — amount 图标处理(忠实复制 digit_probe.py)
- `--field amount` 已支持(读 seat ROI `amount` 框)。
- 新 `--icon-prefix` + `--icon-right-seats`:切格>真值位数时丢图标格;**座有左右之分**
  (`cells[:len]` 右侧图标座 / `cells[-len:]` 左侧),**逐字复制 digit_probe.py:261-263**
  实证逻辑——非自创(我初版 `cells[1:]` 只管左侧+1格,会误采右侧图标座,已纠)。
- 自检回读用 `allow_icon=icon_prefix` 对齐 live(live 是 classify 丢 '?',位置无关)。
- 新 `--validate-file`/`--validate-session`:用建好的模板读**未参与采样**的录像报真泛化准度
  (镜像自检循环;95% 目标看这个数,非自检自洽数)。

### tools\run_build_amount_templates.cmd(新,现成可跑)
- 单行命令(避 `^` 续行空格地狱)。**复用 #229 实测确定值**:源=121925+130651,
  留出验=170343,`--icon-right-seats "5,6,7"`(这桌 5/6/7 座图标在右,已实测非示例)。
  产 `rois\digit_templates_party_poker_8_amount.json`,live 自动接管。

## 3. 为什么是两个工具不是一个

- `digit_probe.py` = 探索/验证(无 `.save()`,#229 在内存里验过 amount ~95-100% 但**没落盘**
  → live 永远用不上,这才是 cut1 真缺口)。
- `build_digit_templates.py` = 持久化(有 `reader.save`)。故 amount 落盘必走它;
  图标逻辑从 probe 忠实搬过来,避免两工具分叉。

## 4. Linux 验证范围(诚实标注)

- ✅ `py_compile` orchestrator + build 工具;`digit_ocr` / `digit_reader` self-test 全过。
- ⚠️ **cv2/numpy 路径(实际采样、读图、相关器)Win-only 未验**——多区加载的文件路由是纯
  Python(可读验),但 `DigitReader.load`/`.read` 的 cv2 部分要 Win 实跑。
- ⚠️ `--icon-right-seats 5,6,7` 是 digit_probe 的**示例值**,非本桌确定真值;**采样自检回读
  会炙出设错的座**(误座读不对)→ 自我修正。

## 5. Win 端下一步(用户执行,结果写 DB 我读)

1. **先查 #229 是否已有 amount 真值文件**(`verified_*` / `truth_amount_*`),有则复用别重标。
2. 采 amount 专用模板:
   `build_digit_templates.py --field amount --icon-prefix [--icon-right-seats <实测座>]
    --harvest-file <amount真值> --out rois\digit_templates_party_poker_8_amount.json`
3. **orchestrator 现在认这个文件名**——放对位置即自动接管 amount 读。
4. 量 amount 准度 vs 真值,**见 95% 再滚下一区**(pot/potprev/herobet 现跨区读尚可,不痛先缓)。

## 6. 严限范围(防外推)

仅 1 种桌皮;amount 模板未采、准度未量(目标 95% 未达,只是把通道铺好);
pot/potprev/herobet 仍跨区读、未独立化(按 measure-then-attack,等 amount 闭环)。
