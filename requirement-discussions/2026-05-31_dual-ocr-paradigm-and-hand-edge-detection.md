# 主题 — 双 OCR paradigm 重设计 + Hand-edge detection + Fold-47% 数据 ground

> **2026-05-31 立**(本 session 用户实操观察 + 数据驱动讨论)
>
> **状态**:讨论 doc,**未实施**。所有断言基于 grep + DB query verified facts;hypothesis 显式标 "未 verify"。
>
> **关联**:[[phase-1-5-attention-mechanism-design]] / [[feedback-data-driven-mandate]] / [[wepoker-card-brightness-variance]]

---

## §1. 用户提议(2 轮)

### §1.1 Round 1 — 双 OCR paradigm 重设计

**OCR-1 角色**(状态机驱动信号源):
1. 一局开始 → 全 seat 扫 timer(最快速度),唯一目标找 timer 位置 → 告知 OCR-2
2. 后续 → 全 seat 扫弃牌,弃牌 seat → 从活动玩家列表剔除

**OCR-2 角色**(持续内容抓取):
1. 收到 timer 位置后立即关注该 seat:动作区 + 下注区 + 弃牌区(暂用头像区代替)
2. 永远只盯 2 个 seat:
   - **current_timer**(timer 在的)
   - **next_active**(活动列表中下一个,德扑顺序)
3. timer 转移时 → 对刚行动的 seat **持续盯 1s**(timer 转移 = 必有动作)

**核心 paradigm**:
- OCR-1 = "谁/什么时候"(信号源 + 活动列表维护)
- OCR-2 = "什么内容"(动作抓取)
- 不重叠覆盖

### §1.2 Round 2 — 边界条件 + 兜底

**边界 1**:**桩位(D)移动 = hand-start**(OCR-1 干)
- 基于用户实测观察:桩位抓取稳定

**边界 2**:**活动玩家头上 "+xx" 显示 = hand-end**
- 筹码结算瞬间出现

**兜底策略**:互为边界
- A 触发 → 上局结束 + 新局开始
- B 触发 → 同上
- 冗余 — 任一漏检测,另一救

---

## §2. 现有 code 状态(grep verified)

### §2.1 现有 hand-start 路径

| Trigger | grep 位置 | 7-day 实际 emit n |
|---|---|:---:|
| Hero card change | _start_new_hand 内 check_hero_cards | observer 模式不可用 |
| Community count 0→0 reset | community_just_reset 信号 | **0** events emit |
| "总底池" label OCR | line 2395 hand_start.via_pot_label | **1** event 7-day |
| pointer.hand_init(内部 emit)| _start_new_hand 末尾 | 830 events |

→ 实际触发路径数据**几乎只有 pointer.hand_init**(无法 distinguish 哪条 signal trigger)

### §2.2 现有 Button detect

```python
# orchestrator.py line 2499+
3 层 fallback (T13 ship 2026-05-28):
  L1: OCR "D" 命中
  L2: brightness peak(button seat 亮度 163-188 vs 非 button 102-119)
       ratio ≥1.3 OR absolute ≥150
  L3: fallback seat=0(diag WARN)
```

**仅在 hand-start 后跑一次**(不持续 monitor button 移动)。

### §2.3 现有 win_amount_area

| 项 | 状态 |
|---|---|
| `SeatROI.win_amount_area` 字段 | ✅ 已定义(`capture/roi.py` line 171)|
| `party_poker_8.json` 包含 win_amount entry | ✅ grep 命中 |
| orchestrator 使用 | ❌ **未使用**(0 grep 命中处理 win_amount_area 的 logic) |

→ ROI 就绪,逻辑空白。

### §2.4 现有 fold detect

```python
# orchestrator.py line 2003-2004
fold_img = self.capturer.capture_roi(seat_roi.fold_area)
fold_text = self.ocr.read_text(fold_img)   # 无 allowlist
# 后续 action_recognizer.parse 判定 "弃牌"/"盖牌"/"FOLD"
```

- fold_area 是**多用途 ROI**(同时识别 timer 数字 / "弃牌" / "All in")
- 没收窄 allowlist
- **当前唯一 fold detect 信号**(无备份信号)
- avatar phash diff(_idle_avatar_hash)**仅用于 showdown gate**,不用于 fold detect

---

## §3. 数据驱动 verify 结果(1196 hands / 7 day)

### §3.1 Button detect 准度

| 方法 | n | % |
|---|---:|:---:|
| L1-OCR | 46 | 3.9% |
| L2-brightness | 1142 | **95.6%** |
| L3-fallback | 6 | 0.5% |
| null_button | 2 | 0.2% |

→ **有效率 99.5%**(L1+L2 命中)
→ Button seat 在 0-8 间均匀分布(125-186 each,seat 8 = 32 因少数 9-max)

### §3.2 Button 连续 hand 间 transition 类型

| 类型 | n | % |
|---|---:|:---:|
| 严格 +1 移动(8-max) | 757 | 63.4% |
| 严格 +1 移动(9-max) | 56 | 4.7% |
| **jumped(跳 ≥2)** | **304** | **25.5%** |
| same_seat(连续 2 hand 同 button) | 76 | 6.4% |
| no_prev(首手) | 1 | 0.1% |

**Findings**:
- "严格 +1 移动" 仅占 68.1%
- "jumped" 占 25.5%(原因未 verify:可能 sit-out 跳过 / 桌型切换 / 偶发误检)
- "same_seat" 6.4%(原因未 verify)

→ **不能假设 button 100% "+1 移动"**
→ 用 **"button 跟上手不同"** 作 trigger 比 "+1" 兜容性强

### §3.3 Fold detect 准度数据

**捕获的 fold(24h sample, 875 events)**:

| OCR 文本 | n | % |
|---|---:|:---:|
| "弃牌" | 871 | 99.5% |
| "盖牌" | 3 | 0.3% |
| "3弃牌" | 1 | 0.1% |

→ 抓到时,OCR 文本 100% 含 fold 字串
→ 7-day fold avg conf **0.912**,p50 **0.85**,conf_low (<0.7) **= 0**

**漏抓数据(180 showdown hands)**:

| Street | captured/hand |
|---|---:|
| preflop | 2.55(主战场) |
| flop | 0.63 |
| turn | 0.22 |
| river | 0.13 |
| **total captured** | **3.34** |
| **silent inferred** | **3.82** |

→ **fold capture rate = 3.34 / (3.34+3.82) = 46.7%**
→ silent fold 跨多街普遍

### §3.4 Active list 维护准度(基于 fold detect 准度)

| 链路 | 数据 |
|---|---|
| user 提议:OCR-2 盯 next_active | 依赖 active list 准 |
| active list 准 | 依赖 fold detect 准 |
| fold detect 准 | **实测 46.7%** |

→ active list 维护链路在数据上**漏剔 53% 的 fold seat**
→ 直接导致 next_active 预测错乱

---

## §4. 矩阵评估(数据 ground 状态)

| 提议 | 数据状态 | 结论 |
|---|:---:|:---:|
| **边界 1**:button 移动 = hand-start | 99.5% detect 准 + 68% 严格 +1 | 数据支持(用"≠上手 button"作 trigger 更稳) |
| **边界 2**:"+xx" = hand-end | 0 数据(从未跑过 win_amount OCR)| **无法 verify** |
| **兜底**:互为边界 | 架构 grep 兼容 | 数据 verify 需 B 上线后 |
| **OCR-2 盯 current(timer 位置)** | timer detect 128 events / 30 min | 数据支持(timer 检测 reliable) |
| **OCR-2 盯 next_active** | fold detect **仅 46.7%** | 数据矛盾(active list 链路从根断) |
| **OCR-1 维护 active list** | fold detect **仅 46.7%** | 数据矛盾(同上) |
| **OCR-1 全 seat 找 timer** | 现有 _shadow_pointer_scan 已实现 | 已 ship 部分 |
| **OCR-1 全 seat 扫弃牌** | 现有 _process_seat_actions fold_area OCR 已实现,但 47% 漏 | 现有机制 = 47%,不会因 paradigm shift 自动改善 |

---

## §5. fold 47% 漏抓的 hypothesis 跟数据状态

### Hypothesis A:OCR 抓到时准,漏在 OCR 没触发(时序漏 / ROI 漏)
- **数据部分支持**:抓到 conf 0.912 高,文本 99.5% 含 fold 字串
- **未 verify**:没"_process_seat_actions 跑过但 fold_area 返空"的 diag instrumentation

### Hypothesis B:overlay 窗口短,4Hz tick 跨不过
- **未 verify**:没 WePoker UI 真显示时长 ground truth
- 推测**没数据支撑**

### Hypothesis C:无 backup 信号(仅 OCR text 单信号)
- **grep 支持**:当前 code 确实仅依赖 OCR
- avatar 变灰 phash diff 字段存在但未用于 fold detect

---

## §6. 数据缺口(实施前需 ground)

per [[feedback-data-driven-mandate]],下列断言需要数据 verify 才能继续:

| 数据缺口 | 怎么取 |
|---|---|
| fold_area OCR 在漏 fold tick 返了什么(空 / 时序错过 / 其他文本)| 加 instrumentation:每 tick fold_area OCR 返空 → emit diag 含 phash diff |
| WePoker UI 弃牌 overlay 真显示时长 | 用户实操观察 + 录屏 ground truth |
| Avatar 变灰 phash diff 在 fold 前后差异 | 加 instrumentation:对比 fold 前后 phash hamming 分布 |
| "+xx" win_amount OCR 准度 | 实际跑一次 OCR + 人工 ground truth verify |
| button "jumped" 25% 原因(sit-out / 误检) | 加 instrumentation + ground truth |
| ROI 是否需要 fold_area + win_amount 单独 calibrate | 用户实操确认 ROI 位置精度 |

---

## §7. 当前 doc 不下结论

per [[feedback-data-driven-mandate]] mandate:
- **不**说 "应该改 paradigm"
- **不**说 "fold 47% 的瓶颈是 X"
- **不**说 "broadcast OCR-2 治根" 或类似 polish 推断

**doc 仅记录**:
1. 用户提议的设计
2. grep verified 的 code 现状
3. DB query verified 的数据 fact
4. 数据缺口清单

实施决策需用户基于 doc + 后续 ground 数据 拍板。

---

## §8. 跟现有 Phase 1.5 v3.2 design doc 的关系

[[phase-1-5-attention-mechanism-design]]:
- §2.4 Pattern D 协作 — 跟用户提议 paradigm 不同(事件驱动 vs 持续盯)
- §3 R 规则盲点 — 部分覆盖(R3 skip 4 类 / R5 sit-out)
- §11 重构清单 — 不含 button trigger / win_amount detect

→ 若用户决定走新 paradigm,**phase-1-5 design doc 需要 update**(可能新 v3.3 版本)
→ 若仍 Pattern D 路径,本 doc 仅作"备选方向 ground"

---

## §9. 实施前置(无论选哪 paradigm)

| 前置 | 依据 |
|---|---|
| Fold detect 47% 治根 | 因为 next_active / Pattern D / silent rate 全依赖 fold detect 准 |
| Instrumentation 加 | 数据缺口 §6 不补,任何方案都妄想 |
| Win_amount OCR 试跑 | "+xx" 边界 2 可行性 verify 必需 |

---

## §10. 沉淀位置

- 完整 doc:`requirement-discussions/2026-05-31_dual-ocr-paradigm-and-hand-edge-detection.md`(本文件)
- memory:无新 memory(per [[feedback-data-driven-mandate]] mandate,无定论不沉淀)
- change-log:无(无代码改动)

---

# 追加(2026-05-31 同 session 续)— code+DB 深挖 + 用户实测 ground truth

> 触发:用户提"新增专职 fold ROI,因弃牌两字稳定显示至本手结束"。下面把这个前提 ground 到底。

## §11. 用户实测 ground truth(最强信号)

用户开着 WePoker 实测一个弃牌对手,回答 3 问:

| 问 | 用户实测答 |
|---|---|
| "弃牌"两字是否整手挂着? | **一直挂着** |
| 头像是否变暗、持续? | **头像亮度同步持续变暗** |
| 显示在 seat 哪个位置? | **头像居中** |

→ 前提**成立**:fold 信号(文字 + 变暗)整手持续,且就在头像居中。

## §12. 代码确认 — 停轮询架构 + fold 检测路径(grep verified)

**① folded seat 一旦检测到就停轮询**(`detector.py:200` + `orchestrator.py:1806`):
```python
def is_skippable_seat(seat_idx):
    if ATTENTION_MODE: return seat_lifecycle.is_skippable(...)
    return seat_idx in self._folded_seats or seat_idx in self._empty_seats
```
- ATTENTION_MODE 默认关(`config.py:43`,env 没设)→ 录的数据走 legacy(folded+empty 跳)
- 后果:检测到 fold → 加 `_folded_seats` → 后续 tick 全跳 → **dedup 数据无法反推 persistence**(1.2% 低 dedup 是架构必然)

**② 对没 fold 的 seat,每 tick(4Hz)扫 fold_area,直到抓到或本手结束**(`orchestrator.py:1801-2036`):
- Branch 0(1987-1999):`timer_area` 单独配则先读;读到 digit(0-60)→ `_process_timer` + **`continue`(跳过 fold 检测)**
- Branch 1(2013-2020):fold_area 读到 digit 且 `parse(ft) is None` → 当 timer + `continue`
- Branch 2(2022-2026):fold_area parse 命中 FOLD/ALL_IN → 记 action(**捕获路径**)
- Branch 3(2030-2036):空 → finalize timer + 更新 idle baseline

## §13. 47% 全量复核(1192 hands,非 180 抽样)

`v_ring_beam_handend_fold_inference` 全量聚合:

| 指标 | 值 |
|---|---:|
| hands | 1192 |
| folds_captured 合计 | 4057 |
| silent_folds_inferred 合计 | 4593 |
| **capture_rate** | **0.469** |
| avg captured/hand | 3.40 |
| avg silent/hand | 3.85 |

→ 47% 不是抽样误差,**全量实测确认**。

## §14. 核心悖论 + 候选漏点

**悖论(实测 + 代码推出)**:
> 信号整手在(§11)+ ROI 框对位置(§11 头像居中 / 代码 `line 1973` "avatar center")+ 每 tick 都扫(§12②)→ 本该 ~100%,实测 47%。

→ **那 53% 不可能是"信号没出现"漏的。是代码路径里某步,在信号在的情况下没读到/没记。**

**候选漏点(代码 verified,非定论)**:
- Branch 0 / Branch 1:folded 头像变暗 + "弃牌"笔画 → 在 timer/fold ROI 产生 **digit 噪声** → 被当 timer `continue` → 整手不再回看
- 这正是"多用途 ROI 污染" → **专职 fold ROI + 收窄 allowlist 恰好能治这一类**
- ⚠️ 但**没数据证明它是 53% 的主因**(漏 tick 不落日志,doc §6 缺口 #1)

## §15. 47% 的 confound(必须标:可能虚低)

`silent_folds_inferred = n_seated − 摊牌人 − captured_fold` 倒推。

- 若有人**坐着没发牌(sit-out)**→ 被算成"silent fold",但他没玩这手、没弃牌
- button "jumped 25.5%"(§3.2)暗示 sit-out 不罕见
- → **真实漏读率可能 < 53%**;此 confound 未用数据剥离,标"未验证"

## §16. T115 插桩方案(已决:🅰️ 先插桩后建 ROL)

**目的**:定位 53% 漏在哪个 bucket,决定专职 ROI 能治多少。

**有界设计**(per `diag.emit` 每条一次 DB insert,只在候选点 emit、每 (hand,seat,reason) 一次、real-player seat only):
在 fold_area 路径加 `fold_probe.miss_candidate` diag,reason 分:

| reason | 含义 | 专职 ROI 能治? |
|---|---|:---:|
| `foldarea_digit_as_timer` | fold_area 读出 digit 被当 timer | ✅ allowlist 治 |
| `foldarea_unparsed` | 非空但 parse 不出 FOLD(如"弃"残字/噪声)| ✅ allowlist + 专 ROI 治 |
| `foldarea_empty` | real-player seat 读出空(信号该在却读空)| ⚠️ 指向 ROI 标定/暗化 OCR,allowlist 不够 |
| (无任何 probe + silent fold) | seat 被 skip 没读 / sit-out 假漏 | 需后续追查 |

**验证流程**:插桩 → 用户录 30 min → join `fold_probe.miss_candidate` 与 `silent_folds_inferred` 的 seat → 看 53% 落哪个 bucket。

**结论门**:
- 主因 = digit_as_timer / unparsed → 专职 ROI + allowlist 治根,建
- 主因 = empty → 专职 ROI 不够,需先标定 ROI / 解决暗化 OCR
- 主因 = sit-out 假漏 → 47% 本身虚低,问题没想象大

## §17. 沉淀位置(更新)

- doc:本文件(含本追加段)
- code:T115 插桩 = DEV(本 session 实施)
- memory:仍无新 memory(主因未定,无定论不沉淀)

---

# 追加 2(2026-06-01 Win 侧实录)— T115 首测结果 + T117 精准探针 v2

## §18. T115 v1 首测(sample=17 手,observer / party_poker_8,不可外推)

录制健康度(实测):17 手 / 79 action / 31 fold captured / conf 0.858 / 91 探针。
ROI 对齐良好(窗口 1454x1287 精确吻合,seats/community/pot/button 全准)。

**v1 探针 reason 分类(91 条)**:

| reason | n | 判读 |
|---|---:|---|
| `foldarea_empty` | 61 | **存疑** — v1 对"还没弃牌的活跃座位"误报(闲置读空也记)|
| `foldarea_unparsed` | 26 | **铁证**:含 `奈牌`(OCR 把"弃"认成"奈")、`FCIB?`、`十` → allowlist 可治 |
| `foldarea_digit_as_timer` | 7 | 多用途污染(`11`/`25`/`正在带入 11`)→ allowlist 可治 |

**数字巧合**:captured 31 ≈ (unparsed 26 + digit 7)=33 → 若这俩是主体,漏的是"信号被认错"非"无信号",user 专职 ROI+allowlist 可把捕获率近翻倍。

**但 crux 未解**:61 条 empty 分不清"闲置噪声"vs"真弃了却读空"。后者 allowlist 治不了(指向 ROI 标定/暗化)。v1 探针无法区分 → 故做 v2。

## §19. T117 精准探针 v2(本 session 实施)

**改法**:per-seat per-hand 累积 fold_area 读取画像 → **hand-end 仅对"确认 silent 弃牌"座位 emit**(`fold_probe.silent_seat`),去掉 v1 闲置误报。
- silent 集 = `range(num_seats) − _empty_seats − _folded_seats − showdown_seats`(含 ≤1 未摊牌赢家 false positive)
- `dominant` 优先级 `unparsed > digit > empty > no_read`:
  - empty 票预期(弃牌前闲置),故排在后;**只有"弃牌后仍只读到 empty"才落 `dominant=empty`** = 信号没读到的真盲区
- 有界:每手 ≤ 入场座位数 条

**结论门(下次录 30 min 后 join)**:
- silent 座位 dominant 多为 unparsed/digit → user 专职 ROI+allowlist 治大头,建
- 多为 empty → 信号没读到,先标定 ROI / 解决暗化,allowlist 不够
- 多为 no_read → 座位被 skip,查 skip 逻辑

## §21. T117 v2 首测(39 手 / 33 min)+ T119 v3 加 player/亮度

**v2 实测 dominant(剥赢家后)**:unparsed 80(干净,allowlist 可治)/ empty ~46(剥 ~34 赢家,集中座位 0/5/7)/ no_read ~30 / digit 2。

**按座位 + 名字 OCR 交叉(关键)**:

| seat | 名字失败 | fold empty | fold unparsed | 读法 |
|---:|---:|---:|---:|---|
| 0 | 58 | 24 | 3 | 名字+fold 双废 |
| 5 | 22 | 20 | 7 | 名字+fold 双废 |
| 7 | **0** | 24 | 5 | **名字好、fold 空** |
| 4 | 1 | 8 | 30 | 名字好、fold 认错 |
| 6 | 1 | 1 | 26 | 名字好、fold 认错 |

- seat 7(名字好、fold 空)**推翻纯头像污染**(污染应出乱码非 empty)→ 指向 fold 区本身
- seat 0/5(名字+fold 双废)**支持用户"那玩家/那位置整体难读"**
- 至少 2-3 种原因,非单一;**座位 vs 玩家在本数据绑死(无人换座)+ probe 无 player → 分不开**

**T119 probe v3(本 session 实施)**:`fold_probe.silent_seat` payload 加 `player`(seat→player_id_map)+ `avg_lum`/`min_lum`(fold_area 区每 tick 累积亮度)。
- 下次录完离线按 player 分组:某玩家跨座位都 empty → 玩家/头像(用户对);某座位换玩家仍 empty → 几何(框偏)
- 按 avg_lum 相关:empty 集中在低亮度 → 暗化污染(用户变体对);empty 与亮度无关 → 几何
- 用户选 B(不截图,避免传输色差/分辨率失真,源头采数据更可信)

---

# 追加 3(2026-06-01)— T119 v3 换桌判别结论 + T120 重框+allowlist 实施方案

## §22. 换桌判别结论(105 条 / 26 手 / 26 玩家 / ~24 min,含换桌)

**实测 per-seat(v3,带 player + 亮度):**

| seat | empty | empty 落在几个不同玩家 | empty 区均亮 | 非 empty 均亮 |
|---:|---:|:---:|---:|---:|
| 0 | 12 | **4** | 130 | 101 |
| 5 | 6 | 3 | 83 | 61 |
| 7 | 14 | **4** | 116 | 128 |
| 4 | 3 | 1 | 155 | 77(主 unparsed×20)|
| 6 | 4 | 2 | 110 | 85(主 unparsed)|

**两条独立证据 → 根因 = 座位几何,非头像污染:**
1. **玩家无关**:换桌后 seat 0/7 坐的是全新的人/头像,**仍 empty(跨 4 个不同玩家)** → empty 只跟座位绑定
2. **亮度无关**:empty 普遍**偏亮**(seat 0/4 empty 比非 empty 还亮)→ ROI 框在明亮空背景上,非"暗头像低对比"

→ **用户头像污染假说被换桌实验否掉**(好实验的价值:能推翻提出者猜想)。
→ 边界:26 手;seat 0/7(12-14×4 人)扎实;seat 1/2/3/6 小样本不下结论(非问题座位)。

**两类漏 + 治法:**
| 漏因 | 座位 | 治法 |
|---|---|---|
| fold ROI 框偏到空背景(几何)| 0, 5, 7 | 重框 |
| OCR 认错(奈/奔)| 4, 6 | allowlist 收窄 |

## §23. T120 实施方案 — 独立 fold ROI + allowlist

### §23.1 设计决策:新增独立 `fold_text_area`,**不动** `fold_area`

依据:
- `fold_area` 是多用途(头像 baseline / 摊牌 CNN / timer fallback / allin / fold),`capture/roi.py:160`。直接改它的框会波及摊牌/头像/timer
- **红线** [[ocr-allowlist-double-edge]]:allowlist 收窄**只能加在单用途 ROI**,加在多用途 fold_area 会把空背景 quantize 成假噪声
- → 唯一正解:**新增单用途 `fold_text_area`**,精确框在"弃牌"文字处,**专配 allowlist `弃牌盖`**,只管 fold 检测;allin/timer/摊牌/头像继续走 fold_area

### §23.2 代码改动(向后兼容:JSON 没配则回落现有 fold_area 路径)

| 文件 | 改动 |
|---|---|
| `capture/roi.py` SeatROI | 加 `fold_text_area: ROIRegion \| None = None` |
| `capture/roi.py` from_json(~125)| `fold_text_area=_tuple_to_roi(s["fold_text"],"seat_fold_text") if s.get("fold_text") else None` |
| `capture/roi.py` to_json(~76)| 序列化 `fold_text_area` → `entry["fold_text"]` |
| `rois/party_poker_8*.json` | 每座加 `"fold_text": [l,t,w,h]`(标定得来)|
| `pipeline/orchestrator.py` fold 路径(~2001)| fold_text_area 存在则**先**读它(allowlist=`弃牌盖`)→ parse 命中 FOLD 即记;否则回落现有 fold_area 逻辑(保 allin/timer 不变)|

允许字符集:`弃牌盖`(强制 OCR 把"奈/奔"收敛到"弃")。allin("ALL IN")不在此集 → 继续由 fold_area 处理。

### §23.3 标定(Win 端,源头取,免截图色差)

- 用 `tools/roi_config.py` 在 Win 实时画面上**逐座位框 `fold_text` 到"弃牌"文字位置**(8 座全框,0/5/7 是已知偏的、必修)
- 实时画面取框 = 无传输失真(呼应用户选 B 的理由)
- 未来可接 #LR14 Dashboard 可视化拖框

### §23.4 验证(回归用同一探针)

- 重框后重录 30 min → 跑 `fold_probe.silent_seat`:
  - 预期 seat 0/5/7 的 `empty` 大幅下降
  - 预期 seat 4/6 的 `unparsed` 大幅下降(allowlist 生效)
  - 预期整体 capture rate 明显上升(历史 47% → ?)
- 探针保留,作重框前后 A/B 度量

### §23.5 回滚

- JSON 删掉 `fold_text` 键 → 自动回落现有 fold_area 路径(零风险)
- 或加 env flag `POKEMIR_FOLD_TEXT_ROI`(默认开,1 行回滚)

### §23.7 首次重框后验证(29 手,2026-06-01 02:09-02:31)

**捕获率**:修前 40.5%(95 手)→ 修后 **49.3%**(29 手)≈ +9pp(含换桌 confound,真实归因不纯)。

**per-seat empty/手 对比**:

| seat | 修前 | 修后 | 判 |
|---:|---:|---:|---|
| 0 | 0.46 | **0.45** | ❌ 没变 |
| 7 | 0.54 | 0.28 | ↓ 改善 |
| 5 | 0.23 | 0.10 | ↓(n=5 太小)|
| 3 | ~0 | 0.72 | ⚠️ 新差(换桌玩家变数)|

**结论**:
- fold_text 小赢(+9pp),**多半来自 allowlist**(治 4/6 的奈/奔认错)
- **0/5/7 几何没真修好** —— 印证 §23.6 风险:首次框把 0/5/7 的 fold_text 框在**旧 fold_area 正中心**(偏移≈0),「弃牌」不在那中心 → 等于没挪 → seat 0 照漏
- per-seat 跨桌有玩家/桌面变数(seat 3 修前净修后差),9pp 含噪声
- **教训**:重框必须**对准屏幕上可见的「弃牌」两字**,不能照头像中心点(「弃牌」整手持续,有时间等)

**下一步**:仅重框 seat 0/5/7(4/6 allowlist 已生效,别动),框时务必等该座有人弃牌、「弃牌」可见。命令见 §23.8。

### §23.8 单座重框命令(per-seat,2026-06-01)

```
python tools/roi_config.py --name party_poker_8 --window "WePoker" --field seat_0 --element fold_text
python tools/roi_config.py --name party_poker_8 --window "WePoker" --field seat_5 --element fold_text
python tools/roi_config.py --name party_poker_8 --window "WePoker" --field seat_7 --element fold_text
```
- 每条:**等该座位有人弃牌、「弃牌」两字亮着** → 拖框紧贴「弃牌」 → SPACE 确认保存
- `--field seat_N --element fold_text` = 只改该座该元素,合并保留其余(不动 4/6 / 其他 ROI)
- 框完 commit `rois/party_poker_8.json` + push → Claude pull 核对 0/5/7 偏移是否真挪了(非≈0)→ 再录 30 min 验证

### §23.9 全 ROI 校准清扫(2026-06-01,用户逐元素肉眼核 + 对称校验)

触发:用户用 `--verify --element X` 逐元素核对,发现多处偏移。一轮清扫:

**① seat_0 的 seated 污染(用户抓出,实锤)**
- `--verify` 发现 seat_0 的 stack/cards/hand_type 偏移
- diff(cf923b3→7f87d5d):stack y 1218→1072、cards y 1093→991、hand_type y 1192→1075,**全部上移 ~100-150px**
- 诊断:旧坐标是 **hero 上桌(seated)底部布局**,被带进了观战 profile。fold_area/timer/action/id 未变(本就观战正确)→ **只这 3 个元素被 seated 污染,已修**
- ⚠️ 注意:seat_0 的 fold 读空 / 名字 OCR 失败是**另一区域 OCR 问题**(id 框正确却仍失败),与本次 seated 污染无关,仍未解

**② 8 座 fold_text 重框 → 对称验证通过**
- 用户严格对准可见「弃牌」逐座重框
- 镜像校验:1↔7 / 2↔6 / 3↔5 的 center_x 和 = **1454(±0)**,Δy≤1;中线 0/4 x=727 → **几何完美**
- 结论:**0/5/7 的 empty 确定不是框歪**(框验证压在「弃牌」上 + 对称)→ 残留 empty = 区域 OCR / 赢家 sit-out confound

**③ timer 偏窄 → 复用 9-max + 对称**
- 实测 8-max timer ~25px vs 9-max ~40px(偏窄,有 2-3 字符截断风险)
- 程序化复用:8max 0/1/2/3/5/6/7 ← 9max 0/1/2/3/**6/7/8**(用户原映射 5←4/6←5/7←6 经校验错了,右侧应 ←6/7/8;偏 400-640px)
- seat 4(顶中,9-max 无对应)手框
- 校验:8 座 timer 镜像 x 和 1455-1456、中线 0/4 x=728、宽度 38-41px **统一** ✓

**④ chore:停止跟踪 34 个 .pyc + ignore logs/**(c2c95f6)— 它们在 .gitignore 规则前就被提交,每次运行 modified、挡 `git pull --rebase`。已 `git rm --cached`,根治。

**校准状态(commits 7f87d5d / cf923b3 / 96531a7 / f2fa4a7 / c2c95f6)**:fold_text 全对称、timer 全对称+加宽、seat_0 去 seated 污染 → **首次全 ROI 验证正确状态**。之前 40.5%/49.3% 均"带病"不算数。待干净 30min 录制给真实捕获率,再决 seat 0 区域问题(🅰️图像增强 / 🅱️收手)。

### §23.10 最终结论(2026-06-01,clean run 35 手)— fold 检测 ROI 层关闭

**全干净校准后的 clean run(35 手,~35 min)**:

| 阶段 | 捕获率 |
|---|---:|
| 历史(带病)| 40.5% |
| fold_text(带病 29 手)| 49.3% |
| **全干净校准 35 手** | **50.2%** |

**核心发现:全干净校准只 +0.9pp(49.3→50.2),纹丝不动。**

**① seat 0 证伪 region 说**:clean run 中 seat 0 silent 掉到 7/35=**0.2/手(全场最低)** ← 之前 ~1/手(22%)。**用户对:是早期框污染(seated 坐标),不是区域 OCR。我的 region 结论收回。**

**② 改善归因**:40.5→50.2 的 ~10pp **主力是 allowlist(治 seat 4 的奈/奔认错)**,全干净校准贡献 ~0(因 seat 0 的旧 empty 本就不是真漏,修框只是把 silent 在座位间重分布,总量不变)。

**③ "干净度量"撞墙 — 探针测不出真漏率**:
- clean run silent_seat:no_read 36 / empty 51 / **unparsed 54** / digit 3
- unparsed 54 条文本全是**倒计时**(`125`=12s / `12s` / `9s` / `115`=11s)—— 探针读 fold_area,行动座位那里是倒计时,污染了 dominant 分类,**盖住弃牌信号**
- 剥 sit-out(no_read)+ 赢家(~1/手)+ timer 噪声(unparsed)后,真漏区间 ~0-70(捕获 50%~100%)→ **无意义,测不准**
- 粗 sanity:captured 3.5/手 vs 典型 ~5-6 弃/手 ≈ **~60-65%(估算,不可作结论)**

**④ VPIP+30% bias 修正**:[[data-reliability-50-70-percent]] §1.5 的恐慌**被 confound 夸大**;真实 bias 未知、更小;评估应在**终点层(VPIP vs 人工)**,非 fold-capture 层。

**→ 关闭决定**:fold 检测 ROI/OCR 能做的做完了。**banked**:fold_text 专职 ROI + allowlist + 全 8 座干净校准(对称验证)+ seat_0 去 seated 污染 + timer 加宽 + .pyc 去跟踪。**残留 ~50% 是 confound(赢家/sit-out/timer 噪声),非可修漏 → 不再投入。** 回 [[phase-1-5-attention-mechanism-design]] 主线。

### §23.11 终点层度量(2026-06-01,clean run 35 手)— 真丢失是"筹码动作"非弃牌

从 fold-capture 层转到终点层,三方法交叉(用户提议):

**方法① 单局复盘**(hand 50456a80,15 动作最激烈):
- 转牌 pot 268→620 **跳 +352,却只记到弃牌** → **丢了一个转牌大加注**(下注者自己"弃了自己的注"= 他其实是 fold 给了未捕获的 re-raise)
- 另有小噪声:重复 check、seq 缺号、flop pot +28 不一致

**方法② 玩家画像**(留泽神牛 34 手最活跃):
- 风格**方向有效**:留泽神牛(NIT,0 激进/32 弃)vs 好好千他们・MajicD77(各 11 激进)清晰可分
- 但激进动作系统性偏少

**方法③ pot-gap 系统度量(最干净,不受 confound 污染)**:
筹码守恒:进池筹码无对应动作 = 丢了动作。**赢家/sit-out 不动筹码 → 此指标天然剥离 confound。**

| status | 转换 | 手 |
|---|--:|--:|
| ok | 150 | 34 |
| **silent_action_detected** | **57** | **26** |
| negative_drift(OCR 噪声)| 20 | 14 |

→ **~25% 的"动筹码"转换丢了动作**(57/227);**76% 的手(26/34)≥1 个筹码动作丢失**。

**🔑 认知翻转(本轮最重要结论)**:
1. **弃牌其实抓得好**(留泽神牛 32/34 弃牌都记到)— 之前"fold silent 52.5%"基本是 confound 幻觉
2. **真正丢的是"动筹码的动作"(call/bet/raise)≈ 25%** — 弃牌不动筹码,不在此列
3. **VPIP 偏移方向也翻转/存疑**:老结论"VPIP 高估 30%"基于被污染的 fold 指标,不可靠;新证据 call/raise 丢 25% → 激进度/入池可能**低估**;漏弃牌推高 vs 漏入池推低,**净方向不确定 → 任何精确 VPIP 偏移数字都不可信**

**→ 可用性结论(跟 [[data-reliability-50-70-percent]] 一致)**:
- 玩家**风格分类(NIT/LAG 方向)可信** — 三方法都支持
- **精确 stats(VPIP/AF 数值)垫着 ~25% 筹码动作丢失,只能 directional**
- **pot-gap ~25%** 是比 fold-capture 干净的真 baseline 指标(后续优化看它,不看 fold-silent)

### §23.6 风险 / 未决

- "弃牌"渲染位置 8 座是否一致偏移、还是各异 → 标定时观察(若规律偏移可批量算)
- `fold_text_area` 与 `action_area`/`fold_area` 是否重叠 → 标定避让
- allin 仍依赖 fold_area 广读 → 不受本方案影响(确认)
- sample:seat 5 与小样本座位的 empty 还需重录后复核

## §24. 沉淀(更新)

- doc:本文件 §22-23
- code(本 session 已 ship):T115/T117/T119 探针;**T120 重框+allowlist 待实施(需 Win 端标定)**
- memory:仍无新 memory(实施未完成,结论待重框验证后再固化)

## §20. 旁路发现 — created_at 列 bug(2026-06-01)

`hands.created_at` 列默认值是**写死常量** `'2026-05-19 08:26:10.951156+00'`(非 `now()` 函数)→ 之后每行 created_at 都冻结在该值。`action_events.created_at` 同病。
- 影响:**仅审计列**,数据本身正常(真实时间在 `started_at`/`timestamp`/`occurred_at`)
- 教训:**单看 created_at 一列差点误判"数据没落库"**(已被 started_at 推翻)→ 复刻 [[feedback-data-driven-mandate]]:单字段不验证不下结论
- 修法(deferred):`ALTER TABLE hands/action_events ALTER COLUMN created_at SET DEFAULT now()`(DEV,需确认无依赖该冻结值的逻辑)
