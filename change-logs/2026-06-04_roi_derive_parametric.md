# ROI 参数化派生引擎 roi_derive(card_marker 锚 + 模板 + 镜像)

## §1 任务概述
治"每座 ROI 手框、跨座不一致、切桌型/分辨率没法系统迁移"。参考之前 ID ROI 按
card_marker 锚重框的思路,推广到其余 ROI。
- 触发:用户 REQ "参考重框 ID,重新标定其他 ROI,先调研给方案" → 选 **B(参数化模型)+ A(amount 修)**。
- 相邻:ID ROI 锚见 [[player-id-recognition-approach]] / card_marker 见 [[card-marker-active-set-pillar]]。

## §2 调研结论(数据驱动,先量再做)
读 `rois/party_poker_8.json` 真实坐标,**不凭印象说"框乱"**:
- **左右座早已近乎镜像**(轴 x≈727):11 个 ROI 字段里 10 个左右对镜像残差 ≤3.2px。
- **唯一真离群是中柱座 s0(hero底)/s4(顶)**——它俩不套左右模板。
- amount 不是 8 座全坏:**列座 s1/2/3/6 派生残差 0-3**,只有 s0=27/s4=187 巨偏、s5/s7 小偏(7/10)。
- 结论:**不需重框 104 框**;每座 ROI = 该座 card_marker 锚 + 组内偏移模板,从参考座抽模板套同组。

## §3 实现
- `tools/roi_geom.py` +3 纯几何原语:`roi_offset`(box−anchor)/`apply_offset`(逆)/`mirror_box_x`(绕垂直轴镜像)。
- `tools/roi_derive.py`(新):
  - 左组 {0,1,2,3} 用参考 s2、右组 {4,5,6,7} 用参考 s6 抽偏移模板,套各座自身 card_marker 派生。
  - 默认 `--dry-run`:打印 派生vs现有残差 + 中柱特例 + 左右镜像一致性自检。
  - `--write`:**"残差≤tol 才替换、超阈保留原值"** 策略 → 写 `party_poker_8_derived.json`(消列座抖动、护 s0/s4 特例)。

## §4 验证(Linux 静态/逻辑)
- 单测 `tests/test_roi_geom.py` 7/7(含新 3 原语:偏移往返、镜像对合、左→右映射)。
- dry-run on party_poker_8:左右镜像一致性 ✅(最大 4px);**78 字段规整、10 超阈保留原值**;
  产物校验 amount/card_marker/结构全未动。
- roi_config.py 加 `--frame`(--verify 叠录制帧而非实时屏幕):ast 语法 + 3.14 help 串检查通过。

## §5 红线
- **R-7(改 ROI 须重跑 profile 验证)**:命中。**本轮只产出 `party_poker_8_derived.json`,
  生产 `party_poker_8.json` 未动**;启用须 Win 端 `--verify` 叠帧核 + `digit_probe/replay` 验读数不退步 → 再替换。
- R-2/R-3/R-9/R-10:未触发。

## §6 破坏性操作
无。新增文件 + 非破坏性产物;生产 profile / DB 未动。

## §7 手动操作指引(Win-only,cv2 框选/预览我够不着)
1. `git pull` 取 `party_poker_8_derived.json` + `roi_derive.py` + roi_config `--frame`。
2. **逐元素肉眼核**(11 个):
   `roi_config.py --verify --name party_poker_8_derived --element <字段> --frame <录制帧.png>`
   重点盯保留原值:s0/s4 的 amount/id/button、s0 fold_area、s5 fold_text。
3. **重框中柱离群**(只剩 s0/s4 几个框):`roi_config.py --name party_poker_8_derived`(改 _derived.json)。
4. **验读数不退步**:`replay_reconstruct.py --profile party_poker_8_derived --conservation --write-db --run-label derived`
   → Claude 读库比 `derived` vs `solve` 守恒率。
5. 不退步 → 改名 `party_poker_8_derived.json` → `party_poker_8.json`(替换生产,正式触发 R-7 完成)。
6. 泛化:同法生成 `party_poker_9` / `_seated` 变体(参数化模型最大红利)。

## §8 后续
- party_poker_9 / seated 变体用同引擎重生成。
- amount 中柱座(s0/s4)若 Win 重框后仍漂,查"下注显示位置是否随金额位数浮动"(可能本就非定框)。
