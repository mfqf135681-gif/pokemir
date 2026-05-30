# Phase 1.5 注意力机制 + 双 OCR 设计 v3

> **2026-05-30 立**(从 memory 移出 — memory bloat 治理)
>
> **作用**:Phase 1.5 v3 实施时的设计指导。memory 仅留 50-line pointer 触发未来 session 看本 doc。
>
> **范围**:
> - ✅ Phase 1.5 v3 实施全部 Step 1-9 遵守
> - ✅ §11.4 9 步执行序是 implementation runbook
> - ✅ §11.3 4 加法陷阱是 implementation 必防红线
>
> **关联**:`memory/phase-1-5-attention-mechanism-design.md`(pointer)/ `memory/ring-beam-inference-design.md`(LR17)/ `memory/data-reliability-50-70-percent.md`(LAG bias 背景)/ `change-logs/2026-05-30_*` T75/T76/T77/T78/T80/T82

---

## §0. 背景 — 为什么要做(T75 数据驱动 verdict)

`memory/data-reliability-50-70-percent.md` §6 原本说"50-70% 是均匀 noise,Phase 1 接受"。

**2026-05-30 T75 verify 推翻**:
- D2 圈梁 silent fold 率 = **52.5%**(captured 2884 / inferred 3184)
- D1 圈梁 非 fold silent 率 ≈ **29.5%**
- **比率 1.78x — fold 显著比其他动作易漏**
- 影响:玩家 VPIP 高估 **30%**(tight 玩家偏移最大)
- **50-70% 是 systematic LAG bias 不是均匀 noise**

→ 不修 Phase 2 必返工 → **Phase 1.5 真值得做**

## §0.5. 2026-05-30 真实数据 verify(v3 → v3.1)

T83 query 现有 DB(254 ticks + 854 hands)推翻 v3 两条核心假设:

### 推翻 1: DB IO 700ms = 凭印象,**真值 0.1ms**
- `db_avg_ms = 0.10` / `db_pct_of_tick = 0%`(254 ticks)
- → **方案 E 异步全 IO 收益 ≈ 0**(详 §2.6 标 obsolete)
- → 工程量 -1.5 周 → **8.5-9.5 周**

### 验证 2: T73 Batch 真效果 **3.7x**(超出预期)
| 阶段 | T72 GPU only | **T73 + Batch** | 提升 |
|---|---:|---:|:---:|
| Tick avg | 5137ms | **1369ms** | **3.7x** |
| Tick median | 3658ms | **538ms** | **6.8x** |
| action_ocr | 711ms | 79ms | **9x** |

### 验证 3: Fold silent 51.9%(LAG bias systematic 复现)
- 854 hands(T75 是 839)新数据 silent_pct = 51.9% vs T75 的 52.5%
- → **-0.6pp 在 sampling error 内**,**bias 真 systematic**,**不是 artifact**

→ **Phase 1.5 v3.1 调整**:删 §2.6 方案 E / 调工程量 / 标陷阱 3 obsolete;**其余设计 100% 保留**(双 OCR + 状态机 + 13 规则 + Multi-pot + 注意力机制 仍 valid)

## §1. 用户实操观察(6 条,5/6 对)

| # | 观察 | verdict | 影响 |
|:---:|---|:---:|---|
| 1 | **开局是 fold 高峰,玩家不等 timer click** | ✅ 部分对 | fold 可提前,但 call/raise/check **不能** |
| 2 | **timer 永远不会同时在 2 个 seat** | ✅ 对 | 状态机 invariant,简化逻辑 |
| 3 | **raise 是多元素联动**(timer/id/筹码/下注区/pot)| ✅ 对 | id_area 多态(id / 加注 / 跟注 / 让牌 / 下注 / 弃牌)|
| 4 | **动画不考虑**(太快不明显) | ✅ 接受 | chip motion 第 4 路信号 删除 |
| 5 | **全局 = 轮询 + pot,专注 = 当前 + 下一 timer 预测** | ✅ 对 | OCR 职责重定义 |
| 6 | **列入当前任务** | ✅ 接受 | Phase 1.5 优先级提升,**项目主线** |

**关键 paradigm 翻新**:
1. "提前 fold" 颠覆 timer-driven → 全局轮询是**必需**,非兜底
2. "下一 timer 监测" 取代固定延迟 X 秒(自适应 X 模型作废)
3. id 区域承担多角色(id 静态 / action 文字动态)

## §2. 架构 v3 — 全局 + 专注 + 圈梁三层

```
┌──────────────────────────────────────────────────┐
│  OCR-1 全局 (CUDA stream 1):                       │
│  ────────────────────────────────                 │
│  ① 8 seat × {timer-region, id-region} 状态扫       │
│      allowlist = "弃牌跟注让牌加下0123456789"        │
│      skip 4 类: folded / all_in / sit_out / empty │
│      解析: timer / 弃牌 / 加注 / 跟注 / 让牌 / 下注 / id │
│  ② Multi-pot OCR: main + side 1-N                  │
│      allowlist = "0123456789.k万"                  │
│  ③ Hand 状态 detect(blind_posting/dealing/acting/showdown)│
│  ④ 输出: seat_states + multi_pot + hand_phase     │
└──────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────┐
│  OCR-2 专注 (CUDA stream 2):                       │
│  ────────────────────────────────                 │
│  ① 当前 timer seat 持续抓 action + amount + chip    │
│  ② 下一行动者 timer 监测(预测切换,取代延迟 X 秒)     │
│  ③ Hero 简化路径(若 hero 不在 hand → skip)         │
└──────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────┐
│  Ring Beam: D1/D2/D3 + D22/D25-28 推断兜底         │
└──────────────────────────────────────────────────┘
```

**Paradigm 核心**:
- **全局 OCR-1 抓 提前 fold + pot 状态**(轮询所有 seat)
- **专注 OCR-2 抓 timer seat 的 voluntary action**(只 call/raise/check)
- **切换 trigger = 下一 timer 出现**(不是固定延迟)
- **id_area 多态**(空闲 = id,行动中 = "加注/跟注/让牌/下注")
- **timer 唯一性 invariant**(0 或 1 个 timer seat per tick)

## §2.4. 双 OCR 协作 — Pattern D(tick-aligned + sync-merge)

**4 选项对比**:A 主从串行(失并行)/ B 共享状态 1-tick 滞后 / C 双向 EventBus(同步复杂)/ **D tick-aligned + sync-merge ✅**

### 每 tick cycle(250ms 边界):
```
Read shared state (from T-1):
  timer_seat, folded_set, hand_phase, last_action_seat
       ↓
并行 GPU(CUDA stream 1 + 2):
  ├ OCR-1 task (stream 1, ~60ms): 全局扫 + multi-pot + hand_phase
  └ OCR-2 task (stream 2, ~10ms): focus seat + 下一 timer 监测
       ↓
torch.cuda.synchronize()
       ↓
Merge to HandStateMachine:
  ① OCR-1 → seat_states + multi_pot + hand_phase
  ② OCR-2 → current_action + next_timer_hint
  ③ Cross-validate(OCR-1 状态变化 + OCR-2 amount → 合成 ActionEvent)
  ④ 冲突 → Ring Beam 仲裁(D1-D28 数学推断)
       ↓
Update shared state for T+1
       ↓
Enqueue events → 异步 IO(§2.6)
```

### 4 类冲突解决
1. OCR-1 看"加注" + OCR-2 没读到 amount → Ring Beam D5 推 amount
2. OCR-1 timer 移到 B + OCR-2 上 tick focus A → 同 tick 并发抓 A final + 切 B
3. preflop 多 seat 同 tick 弹"弃牌" → 全部 record silent fold,timer 位置不重要
4. OCR-1 timer 消失 + OCR-2 没看下一 timer → 触发 hand 状态切换(street / showdown / between hands)

### 资源
- VRAM 总 ~3 GB(两 Reader × 1.5 GB),5070 Ti 16 GB 余 13 GB 给 LR1/LR2/LR4
- GPU 并行率 凭原理 ~80%(Python GIL 限),**未 benchmark 5070 Ti 实测**

## §2.5. 状态机扩展(规则 stress test 后)

### Seat lifecycle(5 状态)— 实施见 `pipeline/state/seat_lifecycle.py`(T80 已 ship)

```
empty → joined → sitting →
  ├ active_pre_action(发牌后等行动)
  ├ acting_with_timer(timer 在他)
  ├ folded(本 hand 终态)
  ├ all_in(本 hand 终态,但仍在 pot)
  └ sit_out(不参与本 hand)
       ↓ between_hands
       回 sitting / 离桌 leaving
```

**OCR-1 skip 逻辑**:
- skip = {folded, all_in, sit_out, empty, leaving}
- scan = {active_pre_action, acting_with_timer}

### Hand 状态机(12 阶段)— 实施见 `pipeline/state/hand_phase.py`(T80 已 ship)

```
between_hands → dealing_cards → blind_posting →
preflop_acting → dealing_flop → flop_acting →
dealing_turn → turn_acting → dealing_river → river_acting →
showdown → settling → between_hands
```

**阶段约束**:
- `blind_posting` 期间 stack 变化 ≠ voluntary action(POST_SB/POST_BB synthetic)
- `dealing_*` 期间 OCR-1 idle(1-3s 发牌动画)
- `showdown` 期间 timer 全消失但 hand 未结束
- `between_hands` 期间 OCR-1 等下一手 timer 出现

## §2.6. ~~异步全 IO(方案 E)~~ — **OBSOLETE (2026-05-30 v3.1)**

### 为什么 obsolete

v3 § 2.6 假设"DB IO 700ms / tick" → T83 真实数据 verify(254 ticks):
- `db_avg_ms = 0.10` / `db_pct_of_tick = 0%`
- **DB 比假设小 7000 倍**(可能 connection pool 复用 + 本地 INSERT 已治根)

→ 方案 E 异步全 IO **收益 ≈ 0**(不存在的瓶颈无法异步治根)
→ 4 方案 A/B/C/D/E **全部 obsolete**

### T82 模块怎么办

`pipeline/io/diag_queue.py` + `db_queue.py`(T82 已 ship)**保留作 LR backlog**:
- DiagQueue 可能在 diag.emit JSONB serialization 真成瓶颈时再启用(需独立 benchmark)
- DBWriteQueue 留作未来 DB 真出瓶颈时备选
- **当前不集成 orchestrator**

## §3. 13 个规则盲点(德扑 stress test verified)

### 🔴 严重 5 个(必修 — 影响 paradigm)

#### R1: **提前 fold 范围限于 fold**
- fold:online 允许提前 click
- call/raise/check:**必须按行动顺序**
- → OCR-1 全局抓 fold,OCR-2 focus 抓 voluntary action
- → +0 周(架构本身保留区分)

#### R2: **SB/BB post ≠ voluntary action**
- post 时 stack 减少 0.5/1 BB,**不消耗 timer**
- T65 已 synthetic 注入,但全局 OCR 在 blind_posting 阶段**必须忽略 stack delta**
- → +0.3 周

#### R3: **skip 4 类(不只 fold)**
- folded + all_in + sit_out + empty(+ leaving)
- all-in 仍在 hand(showdown 亮牌),但不再行动
- → +0.3 周

#### R4: **Multi-pot(main + side 1-N)**
- 多 all-in → side pot
- 你说"实时 + 总"漏 side pot(可能 1-3 个动态)
- 跟圈梁 D28 边池分解 corroborate
- → +0.5 周

#### R5: **sit-out vs fold vs leave 区分**
- sit-out:不发牌,不出现"弃牌"字
- fold:有"弃牌"字
- leave:头像消失/变灰
- → Seat lifecycle 状态机 cover
- → +0.3 周

**严重小计 +1.4 周**

### 🟡 中等 6 个(Phase 1.5 完成前修)

#### R6: **check 是消极行动**(用户"多元素联动"漏)
- check 时 stack 0 变化,pot 0 变化,id 区显示"让牌"
- 唯一信号:**timer 移走 + id 区"让牌"字**
- 不是"多元素联动",是**单元素**
- → "联动" 描述只对 call/raise/bet 成立

#### R7: **BB option auto-check**
- BB preflop 没人 raise → 默认 check
- WePoker UI 可能**不显示"让牌"字**
- 圈梁 D22 已 cover,但 OCR-1 看不到信号

#### R8: **Time bank UI 变色**
- 玩家用 time bank → timer 数字颜色可能变
- OCR-1 状态机需鲁棒
- 数据增广

#### R9: **Showdown 阶段**
- 最后一街结束 → 摊牌
- 全 timer 消失,但 hand 未结束
- → Hand 状态机 "showdown"

#### R10: **街切换**(flop/turn/river)
- 每街开始全场 timer 重置,从 SB 开始
- 公牌发 1-3s 动画
- → Hand 状态机 "dealing_board"

#### R11: **断网 auto fold/check**
- 玩家断网 → timer 走完 → auto action
- WePoker 可能显示"断线"或灰头像

**中等小计 +1.5 周**(分摊到状态机扩展)

### 🟢 小盲点 3 个(顺手补)

#### R12: 跨手 buy-in stack 增加 → 不是 action
#### R13: Hero seat 简化(hero 不 silent)
#### R14: 发牌动画期间 OCR 噪音

## §4. 12 提升空间(Tier 分级,**chip motion 已删**)

### Tier 1: Sprint 1 同 Phase 1.5(+1 周,从 +2 周降)
1. ~~chip motion 第 4 路信号~~ ❌ 用户否决"动画太快不明显"
2. **OCR-1 → CNN 状态分类**(8-class CNN 替代 OCR,30ms → 3ms)
3. **持续焦点抓取**(reframe,0 增量)

### Tier 2: Sprint 2 / Phase 2 启动(+5.5 周)
4. **多帧 OCR vote**(同 ROI 连续 3 帧投票)
5. **玩家个体 base rate 学习**(但 X 自适应已 obsolete,改 think 时长建模)
6. **跨 hand 状态 carry-over verify**(D8 cross-hand)
7. **timer 倒计时数字 → 切换预测**(提前 N 秒 buffer)
8. **历史 hand 异步补 silent**(Phase 1 dashboard 接受异步 fill)

### Tier 3: Long-term backlog
9. HUD overlay 低置信度标识(LR5)
10. 玩家 self-correction loop(LR12)
11. 多桌 / 多 profile scaling 预留
12. 多机分布式(LR15 期间做)

## §5. 工程量 v3.1(2026-05-30 数据 verify 后)

| 工作 | 周 |
|---|:---:|
| 全局轮询(状态机 + 跳 4 类)| 1.5 |
| Multi-pot OCR(R4)| 0.5 |
| Hand 8 阶段状态机(R2/R9/R10) | 1 |
| Seat 5 状态机(R3/R5) | 0.5 |
| 下一 timer 监测 | 1 |
| 焦点切换(无延迟) | 0.5 |
| ROI 布局 verify(id 多态) | 0.5 |
| Tier 1 提升(#2 CNN + #3 持续) | 1 |
| 中等盲点 R6-R11 修补 | 1.5 |
| **Pattern D 双 OCR 协作(stream 并行 + state-sync merge)** | **+0.5** |
| ~~方案 E 异步全 IO~~ **obsolete**(详 §2.6)| ~~+1.5~~ **0** |
| 测试 + tune + ground truth | 1 |
| **合计** | **9.5 周** |

→ 真工程量 **9.5-10.5 周**(v3.1 8.5-9.5 周 + §12 摊牌专项 1 周;原 v3 10-11 周 - 方案 E 1.5 周 + §12 1 周)
→ **silent < 3% 仍可达**(主因是状态机 + 双 OCR + 13 规则,不是异步 IO)
→ **摊牌 2+ 玩家亮牌率 12% → 80%+**(v3.2 新增 §12 治根)

**前置 verify**:
- **1 week 全状态过渡录屏**(post/check/showdown/街切换/all-in/sit-out/time bank/断网/BB auto check/side pot)
- ~~0.5 day Win 端 IO benchmark~~ **obsolete**(DB 已 0.1ms,无需 benchmark)
- **0.5 day CUDA stream 真并行率 benchmark**(验证 80% 假设)

## §6. 触发条件(2026-05-30 共识 update)

**项目主线优先级** — Dashboard 不再是硬前置,**全部满足即启动**:
1. ✅ T75 verify LAG bias systematic(已满足)
2. ✅ T83 verify DB IO 0.1ms(方案 E obsolete,已满足)
3. ⏸ Win 端 1 week 全状态录屏 verify
4. ⏸ Win 端 0.5d CUDA stream 真并行率 benchmark
5. ⏸ ROI 布局确认(id_area 多态 verify)

**永不优先级**:Win 端 verify 完成之前不启动实施(防盲实施)

**共识基础**:self-use 项目质量本身 = user value;T75 数据驱动结论不需用户反馈背书

## §7. 跟其他 LR 关系

| 关系 | 内容 |
|---|---|
| **跟 `memory/ring-beam-inference-design`** | 全局 OCR-1 抓持久态(状态/pot/skip 4 类)+ 圈梁 D1-D28 后端推断,前后端兼容 |
| **跟 #LR17 圈梁** | 圈梁 = 后端数学反推,Phase 1.5 = 前端 UI 状态机抓取,**互补**;圈梁 D22/D28 cover R7/R4 |
| **跟 #LR15 DDD** | Seat lifecycle + Hand 状态机 = DDD aggregate,Phase 1.5 是 DDD 的 Phase 1 SQL/Python 实现 |
| **跟 #LR1 Florence-2** | OCR-2 未来可换 specialization 模型(Tier 2/3) |
| **跟 #LR2 PaddleOCR** | OCR-2 升级路径 |
| **跟 `memory/gpu-ocr-enable-sop`** | 回滚 — POKEMIR_ATTENTION_MODE=0/1 env var |
| **跟 #LR4 rake** | Multi-pot OCR 给 rake 反推提供数据 |

## §8. 关键护栏

### 1. ~~Phase 1 必须先 ship~~(2026-05-30 共识取消)
- 旧:Dashboard MVP 先 ship 再修地基
- 新:**质量地基先 ship**,Dashboard 后做时直接受益 90%+ 数据
- 原因:T75 数据驱动结论 ≠ 用户使用反馈才有,质量本身 = self-use 项目 user value

### 2. 全状态录屏 verify 必做前置(1 week)
- post → preflop → flop → turn → river → showdown
- 各阶段 UI 序列 + timer 时序
- all-in / sit-out / time bank / 断网 / 街切换中间态
- side pot 显示
- BB auto check
- 若假设错 → 整套设计 reframe

### 3. 回滚机制必做
- POKEMIR_ATTENTION_MODE env var
- 0 = 旧架构,1 = 新架构
- 部署默认 0,实测稳定切 1

### 4. 测试 ground truth 必做
- 录屏 10 hand 手工标 action sequence
- 跑新旧对比 silent 率
- 不用 ring beam self-validate(循环依赖)

### 5. 工程量诚实
- v1 5-6 周 → v2 8-9 周(规则盲点真补)→ v3 10-11 周(+ IO + 双 OCR 协作)→ **v3.1 8.5-9.5 周**(数据 verify 后砍方案 E)
- 不接受"几天搞定"压力
- 用户实操经验 ≠ 完整规则覆盖
- **凭印象的"瓶颈估算"必须 data verify**(我 v3 凭印象估 DB 700ms,真值 0.1ms — polish trap 案例)

## §9. 信心 + 不确定

**我的信心**:**78%**(v2 80% → v3 78% — 加 IO benchmark + CUDA stream 真并行率两条未验)

**不确定项**:
- 🟡 全状态过渡时序(needs 1 week verify)
- 🟡 id_area 多态 ROI 布局(needs Win 端 verify)
- 🟡 side pot 显示方式(WePoker H5 实际)
- 🟡 time bank / 断网 UI 表现
- 🟡 BB auto check 是否有"让牌"字
- ✅ ~~DB IO 700ms~~ — **T83 verify 真值 0.1ms,方案 E obsolete**
- 🟡 **CUDA stream 真并行率 80%(needs 5070 Ti 实测)**
- ~~方案 E 异步队列下 silent detection 状态回流~~ — **obsolete**
- 🟢 timer 唯一性(用户实操 confirm)
- 🟢 fold = timer 同位置(用户实操 confirm)
- 🟢 动画不可用作信号(用户实操 confirm)

## §10. How to apply

| 触发场景 | 应用 |
|---|---|
| 用户问 "silent action 怎么办 / overlay 漏抓" | 推 Phase 1.5 双 OCR 架构 |
| 用户问 "GPU 没用满 / 双 OCR 行不行" | 推 架构清晰 + 错峰避撞,**不为用满 GPU** |
| 用户问 "5-6 周还是几天" | 真 10-11 周(stress test 后),凭 verify 不凭印象 |
| 用户问 "提前 fold 怎么办" | 全局 OCR-1 抓,**只 fold 可提前**,call/raise/check 不可 |
| 用户问 "side pot / 边池" | OCR-1 multi-pot + 圈梁 D28 双重 |
| 用户问 "状态机怎么设计" | Seat 5 + Hand 12 阶段(详 §2.5,实施 `pipeline/state/`)|
| 我自己想 brainstorm 新提升 | 先看 Tier 1-3,**不重复发明** |
| 我想说 "动画给信号" | ❌ 用户已否决 chip motion |

## §11. 重构清单 + 加法陷阱(2026-05-30 T78 — 现状 grep 后)

### §11.1 现状基线(grep verified)

| 指标 | 数 |
|---|---:|
| `pipeline/orchestrator.py` 行数 | **2344**(单 monolith)|
| `diag.emit` 调用点 | **42**(分散全 pipeline)|
| Seat 状态 sets | 3(`_folded_seats` / `_empty_seats` / `_pointer_state`)|
| OCREngine 实例 | 1(line 185)|
| 主循环 `_tick()` | line 252 |

→ orchestrator 2344 行 monolith,**任何 v3 改 = 大手术**

### §11.2 12 冲突 → 8 替换 / 6 净加 / 5 净减

#### 🔄 8 处替换(删旧 → 加新,接近 1:1)

| 删 | 加 | LOC delta |
|---|---|---:|
| `OCREngine` 单实例 | `OCREngineRegistry` 2 实例 | +20 |
| `_folded_seats`+`_empty_seats`+散点 skip | `SeatLifecycle` 5-state enum + `SeatStateMachine` | +40 |
| ~~42 处 inline `diag.emit`~~ | ~~42 处 `diag.enqueue` + bg~~ **obsolete §2.6** | ~~+50~~ 0 |
| ~~sync DB session.add+commit~~ | ~~async `DBWriteQueue`~~ **obsolete §2.6** | ~~+70~~ 0 |
| T52 `_capture_with_diff_trigger` 整段 | OCR-1 状态机内"状态未变跳过" | -60 |
| T63 `_detect_empty_seats` 独立 | `SeatLifecycle.transition_to_sit_out` | -10 |
| T65 `_inject_post_events` 独立 | `HandPhase.BLIND_POSTING` enter handler | -10 |
| T73 `_pre_batch_action_amount_ocr` 单 OCR | OCR-1 batch + OCR-2 batch | +10 |

**替换小计 +110**

#### ➕ 6 处净加(必须的新功能)

| 净加 | 理由 | LOC |
|---|---|---:|
| `HandPhase` 8-state enum + state machine | R2/R9/R10 盲点 | +200 |
| `OCR_1_StateScanner`(timer-region 状态扫)| 新职责 | +150 |
| `OCR_2_FocusTracker`(focus + 下一 timer 监测)| 新职责 | +150 |
| Multi-pot ROI + OCR | R4 盲点 | +100 |
| CUDA stream × 2 + barrier | 基础设施 | +80 |
| `HandStateMachine.merge()`(Pattern D 仲裁)| 新协作层 | +150 |

**净加小计 +830**

#### 🗑️ 5 处净减(用户"加法病"警告对偶 — **真有减法**)

| 净减 | 删除原因 | LOC |
|---|---|---:|
| T52 `_capture_with_diff_trigger` | OCR-1 状态机替代 | -60 |
| `_shadow_pointer_scan`(line 1552+)| OCR-1 直接读 timer | -50 |
| `_pointer_state` 推断逻辑 | OCR-1 直接给 timer_seat | -40 |
| 散点 `if sidx in _folded_seats: continue` × N | SeatStateMachine 统一 | -30 |
| 散点 `diag.emit` formatting 重复 | enqueue 接口统一 | -20 |

**净减小计 -200**

**总净 LOC**:**+740**(30% 增,2344 → ~3084)

### §11.3 ⚠️ 4 个加法陷阱(必防 — 实施时 reflexive check)

未来实施时**必须警觉这 4 个陷阱**:

#### ⚠️ 陷阱 1: 双轨保留 sets
- ❌ 既 keep `_folded_seats` 又加 SeatLifecycle
- ✅ **强制删旧 sets**,不留 fallback
- 检测:grep `_folded_seats` 应为 0 命中

#### ⚠️ 陷阱 2: diag.emit 半改
- ❌ 改 20 处 enqueue,留 22 处 inline("那几处不重要")
- ✅ **42 处全改**,bg writer 接所有
- 检测:grep `diag.emit\|diag\.emit` 应仅在 `DiagEmitter.enqueue` 内部 1 处

#### ~~⚠️ 陷阱 3: sync DB 留 escape hatch~~ — **OBSOLETE (2026-05-30 v3.1)**
- 方案 E 已 obsolete(DB 0.1ms 无需异步),陷阱 3 自然 obsolete
- DB 保持 sync 是正解,不是陷阱

#### ⚠️ 陷阱 4: T52/T63/T65/T73 旧 patch 不删
- ❌ "留着保险" / "万一新架构不稳"
- ✅ **回滚靠 `POKEMIR_ATTENTION_MODE=0` env var**,不靠双轨代码
- 检测:grep T52/T63/T65/T73 标记应 0 命中

→ 4 陷阱本质都是**双轨保留 = 加法病**

### §11.4 9 步执行序(implementation runbook)

```
Step 1: env var POKEMIR_ATTENTION_MODE 加 + 旧路径全包 if-else(0.5d)
Step 2: 拆 orchestrator.py → 3 模块(0.5w,原 5 模块 IO 部分 obsolete)
  ├ pipeline/state/seat_lifecycle.py    (SeatStateMachine) ✅ T80 ship
  ├ pipeline/state/hand_phase.py        (HandPhaseMachine) ✅ T80 ship
  ├ pipeline/io/{diag,db}_queue.py      ✅ T82 ship,但 LR backlog 不集成
  └ pipeline/orchestrator.py            (瘦身,仅 tick 调度)
Step 3: 双 OCR + Pattern D 协作(1.5w)
Step 4: Multi-pot OCR(0.5w)
Step 5: 13 规则盲点(3w)
Step 6: Tier 1 提升(1w)
~~Step 7: 异步全 IO 接入~~ obsolete(详 §2.6)
Step 8: 删旧 patch(T52/T63/T65/T73 旧路径)+ 3 陷阱 grep 检测(原 4)(1w)
Step 9: ground truth verify + tune(1w)
─────────────────────────────────
Total: 8.5-9.5 周(v3 10-11 周 - 1.5 周 Step 7)
```

**已 ship Step 2 部分**:T80(状态机模块)+ T82(IO queue 模块)= 4 个 standalone 模块,**零集成**,等 Win 端 verify 后一次性 wire-in。

### §11.5 4 项待 verify(不能凭印象)

| 项 | 影响 | 验证方式 |
|---|---|---|
| ROI 划定 timer / id / action 是否分离 | 可能合并 ROI | Win 端 ROI 截图比对 |
| `_pointer_state` 是否仅用于 timer 推断 | 删除前提 | T79 audit 已完成 — 5 sites,仅 timer 用途 ✅ |
| 42 处 `diag.emit` 是否都 non-critical | async 前提是非顺序敏感 | T79 audit 已完成 — 全 trace ✅ |
| `_canonicalize_player_id_map` 是否 tick-blocking | 异步化收益 ↑ | benchmark |

## §12. 摊牌捕获专项(2026-05-30 T87/T88 数据驱动加入)

### §12.1 真问题(T87 verify)

138 摊牌 hand 全数据:
| 指标 | 数 | 比例 |
|---|---:|:---:|
| ≥ 1 玩家亮牌 | 128 | 93% |
| **≥ 2 玩家亮牌** | **17** | **🔴 12%** |
| ≥ 3 玩家亮牌 | 1 | <1% |
| 平均亮牌 / hand | 1.06 | — |

**真理论值**:摊牌通常 2-4 玩家亮牌,**真捕获率 ≈ 1.06 / 2.5 = 42%**
**失 ~58% 摊牌信息**

### §12.2 摊牌信息密度(为啥必修)

| 信息源 | bits | 画像影响 |
|---|:---:|:---:|
| Raw action(check/fold)| ~2 | 中 |
| Raw action(raise/bet)| ~3 | 大 |
| **摊牌一张亮牌** | **6-8** | **极大** |
| **整 hand 反推 range** | **20+** | **巅峰** |

→ 摊牌 1 张 ≈ 4 个 raw action 价值
→ 58% 摊牌损失 = 玩家画像最致命 bias 源

### §12.3 真治根方向(非 OCR-1 接管 — 错位过)

摊牌是 **CNN 卡识别任务**(不是 OCR — EasyOCR 不识卡片)。
真问题:**CNN 摊牌路径自身不够 aggressive**

| 当前 | 改为 |
|---|---|
| CNN throttle 1/s/seat | **4/s/seat**(0.25s) |
| river 街被动 trigger | **state machine 主动驱动**(reach showdown seat 锁定)|
| 单帧 CNN | **多帧 fusion**(0.5s 内 3 帧 majority vote)|
| fold_area diff trigger | **SeatStateMachine 提供 reach-showdown 信号** |

### §12.4 预期效果

- 1 玩家亮牌率:93% → **99%**
- 2+ 玩家亮牌率:12% → **80%+**
- 3+ 玩家亮牌率:<1% → **60%+**
- 平均亮牌 / hand:1.06 → **2.0+**

→ **玩家画像最大 bias 源治根**

### §12.5 实施位置

```
Step 5.5(13 规则盲点之后插入):摊牌专项(+1 周)
  ├ SeatStateMachine.reach_showdown_seats 新增 set
  ├ HandPhase.SHOWDOWN enter handler 锁定目标 seats
  ├ CNN throttle 1/s → 4/s 配 ATTENTION_MODE
  └ 多帧 fusion(连续 3 帧投票)
```

### §12.6 工程量更新

**v3.1 → v3.2**:**总 9.5-10.5 周**(原 8.5-9.5 周 + §12 摊牌专项 1 周)

### §12.7 自查 § 11.3 加法陷阱

- ✅ 数据驱动(12% / 1% 真不可接受)
- ✅ 治根方向对(CNN 调度而非 OCR)
- ✅ 跟 SeatStateMachine 兼容(reach-showdown 信号自然来)
- ✅ 替换 throttle 而非双轨保留
- ✅ ROI 极高(摊牌 = 信息密度最高)

→ **真不是加法陷阱,是数据驱动必修盲点**

### §11.6 真治根:必须拆 monolith

orchestrator.py 2344 行 = 历史累积 patch
v3 实施 + 拆 5 模块 = **一次性偿还技术债**
不拆 = v3 后变 3084 行更难维护

**关键**:Step 2 拆模块 **不是 over-engineering**,**是 v3 实施的前提**

---

## 跟 memory 的边界

| 项 | 归属 |
|---|---|
| **本 doc(设计 + 规范 + runbook)** | `requirement-discussions/` — version-controlled with code |
| **memory pointer** | `~/.claude/.../memory/phase-1-5-attention-mechanism-design.md` — 50-line trigger + 关键 reflexive checks |
| **实施代码** | `pipeline/state/` + `pipeline/io/` — T80/T82 已 ship |
| **change-logs** | `change-logs/2026-05-30_*` — T75/T76/T77/T78/T80/T82 |
