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

## §3 实现(镜像派生:单左模板 → 全座统一)
- `tools/roi_geom.py` +3 纯几何原语:`roi_offset`(box−anchor)/`apply_offset`(逆)/`mirror_box_x`(绕垂直轴镜像)。
- `tools/roi_derive.py`(新):**单一真相源 = 左模板 T_L(从 left-ref s2 抽)**。
  - 左型(左列 1/2/3 + hero s0)套 T_L;右型(右列 5/6/7 + 顶 s4)套 `mirror(T_L)`
    (右模板解析镜像 `dx_R=cm_w−w−dx_L`,相对自身锚、无需轴;**镜像保 w/h → 左右必然同尺寸**)。
  - 策略:**列座 {1,2,3,5,6,7} 强制派生(求统一)**;**中柱 {0,4} 残差≤tol 才替换、超阈保留原值**(护真离群)。
  - 默认 `--dry-run`;`--write` 写 `party_poker_8_derived.json`(不覆盖生产)。
  - (演进:初版用 s2/s6 两套独立模板 + 跳过 amount,左右尺寸不一致 8 字段;改镜像派生后 11 字段全统一。)

## §4 验证(Linux 静态/逻辑)
- 单测 `tests/test_roi_geom.py` 7/7(含新 3 原语:偏移往返、镜像对合、左→右映射)。
- dry-run on party_poker_8:**11 字段列座尺寸全统一**、左右镜像一致性 ✅(最大 0px);
  **81 字段派生、7 超阈保留原值**(仅中柱 s0/s4 的 amount/id/fold_area/button);amount 列座统一 77×25;
  card_marker(锚)/结构未动。注:amount s7 位置残差 21px(强制拉到镜像对称点,Win 须核仍盖住下注数)。
- roi_config.py 加 `--frame`(--verify 叠录制帧)+ 画框计数/分辨率告警自诊断 + verify+frame 跳选窗:ast 语法 + 3.14 help 检查通过。
- **Win 实测(用户)**:① 实时观战逐元素 `--verify --element` 叠框肉眼核,列座统一框、位置 OK;
  ② replay 同 session(170343)派生 profile vs 生产(run_label derived vs solve)→ **34 手守恒
  chip_movement/status 逐手 bit 等价(全 delta=0)**,OK 率均 47.1% → **规整化读数零回归,确认安全**。
  注:守恒只读 stack,故此对比证 stack ROI 等价;amount/id 等靠 ① 的肉眼核覆盖。

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

## §8 收口 + 后续
**收口判定**:派生 vs 生产守恒逐手等价(零回归)+ 用户实时眼验通过 → **规整化达成目标
(几何干净/可迁移)且安全,可替换生产**。本质=清理几何,非提捕获率(捕获率瓶颈在 §15 重建,非 ROI)。
- **已替换生产**(2026-06-04,**Linux 侧** cp _derived→party_poker_8.json + git rm _derived + push,
  尊重代码单向流 Linux→git→Win;Win 仅 `git pull`,**不在 Win 改+反向 push**)。旧版存 git 历史。
- 中柱 s0/s4 那 7 个离群框(amount/id/fold_area/button)可选重框(在生产 profile 上),不做亦不劣于现状。
- party_poker_9 / seated 变体用同引擎重生成(参数化模型最大红利)。
- amount 中柱(s0/s4)若重框后仍漂,查"下注显示位置是否随金额位数浮动"(可能本就非定框)。
