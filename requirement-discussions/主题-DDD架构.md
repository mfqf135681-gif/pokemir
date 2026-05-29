# 主题 — DDD 架构(Hand / Player / Seat Aggregates + Event Sourcing)

> **2026-05-29 立**。用户闲聊点醒"扑克本身是状态机"后,做的 DDD 设计 doc。
>
> **作用**:Path B 启动时按本文档建模,**不再走 Path A 的"暴力 poll + 加规则 patch"老路**。
>
> **范围**:
> - ✅ Path B / Path C / Path D 新代码遵守
> - ✅ #LR4 / #LR5 / #LR7 等远期 LR 实现时参考
> - ❌ Path A 已完成代码**不重构**(沉没成本,等 #LR15 触发再说)
>
> **关联**:`[[long-term-roadmap]] #LR15` · `[[dev-rule-validate-blind-spots]]` · T49 task

---

## §1. 设计哲学:领域驱动 而非 信号驱动

| 旧思维(Path A)| 新思维(Path B 起)|
|:---|:---|
| 信号驱动:OCR / phash 抓什么,代码处理什么 | **领域驱动**:扑克本身是什么,代码就是什么 |
| 暴力 poll 所有 ROI / tick | 用领域状态机决定该看哪 ROI |
| 散点 cross-validation (P1-P4) | Aggregate invariant 自动 enforce |
| symptom-driven patch 修 bug | Event sourcing 自然消除一大类 bug |

**核心信条**:**让代码结构匹配领域结构**(textbook DDD)。

---

## §2. 核心 Aggregates(3 个)

### 2.1 Hand Aggregate(主)

**职责**:一手扑克的完整生命周期 + 状态 + 不变量。

```
Hand
├── id (UUID)
├── table_id, num_seats
├── button_seat
├── blinds {sb_seat, bb_seat, sb_amount, bb_amount}
├── community_cards [...]
├── pot_size_current(实时)
├── pot_size_previous_street(上街冻结)
├── street: enum {preflop, flop, turn, river, showdown}
├── active_pointer: seat_idx | None(当前 to act 玩家)
├── seats: dict[seat_idx, Seat Entity]
├── event_log: [DomainEvent...](**主存储,顺序不可变**)
└── status: enum {in_progress, completed, abandoned}
```

**状态计算**:任一时点 Hand state = replay(event_log)。**不存 mutable state,只存 events**。

### 2.2 Player Aggregate(跨 hand 持久)

**职责**:玩家身份 + 跨手 stats + 风格画像。

```
Player
├── identity:
│   ├── primary: avatar_phash (64-bit)
│   ├── label: nickname (OCR'd 可选,人类可读)
│   └── alias_history: [old_phashes, old_nicknames](玩家换头像 / OCR 漂移)
├── stats_projection:(read-side,跨 hand 累加)
│   ├── hands_played
│   ├── vpip_by_position (dict[Position, ratio])
│   ├── pfr_by_position
│   ├── af_by_street (dict[Street, ratio])
│   └── net_winnings_bb
├── style_classification:(computed from stats)
│   └── enum {TAG, LAG, Fish, Nit, Maniac, Unknown}
└── session_state:(当前 session per seat assignment)
    └── current_seat: seat_idx | None
```

**Identity Reconciliation**(Domain Service):
- New observation: (phash, nickname) → 查 Player Aggregate
- 命中 phash → 同玩家,append observation
- 命中 nickname 但 phash 漂移 → 可疑,加 alias_history
- 全新 → 创建新 Player

→ T29 `find_player_aliases.py` 是这个 service 的雏形。

### 2.3 Seat Entity(per hand,不跨 hand)

**职责**:1 个 seat 在 1 手中的状态机。

```
Seat
├── seat_idx (0..n-1)
├── player: Player ref (可空 = 空座)
├── state: enum
│   ├── empty(无人坐)
│   ├── sitting(已就座但本手未行动 / 等待)
│   ├── acting(当前 timer 亮起,在思考)
│   ├── folded(已弃牌,本手剩余 tick 不再行动)
│   ├── all_in(已全押,本手剩余 tick 不再行动)
│   └── auto_fold(离桌 / preset 自动弃牌,无 timer)
├── stack_initial(hand-start 时筹码)
├── stack_current
├── street_actions: [Action...](本街已行动)
└── total_bet_this_hand: float
```

**State machine 状态转移**:

```
empty ──玩家坐下──→ sitting
sitting ──hand_start──→ sitting (等行动)
sitting ──timer 亮──→ acting
sitting ──timer 没亮但 fold_text──→ auto_fold(70% 的 fold)
acting ──玩家行动──→ {sitting (call/check/bet/raise) | folded | all_in}
folded / all_in / auto_fold ──hand_end──→ sitting(下手开始)
```

→ T46-A(fold + empty 统一)+ T48(pointer)是这个 entity 的雏形。

---

## §3. Domain Events(11 个核心事件)

```python
# 不可变 dataclass,只用于 event sourcing
HandStarted(hand_id, button_seat, num_seats, blinds, players_at_seats, started_at)
BlindsPosted(hand_id, sb_seat, sb_amount, bb_seat, bb_amount)
HoleCardsDealt(hand_id, hero_cards)  # 仅 seated mode
PlayerActed(hand_id, seat_idx, action_type, amount, street, confidence)
TimerObserved(hand_id, seat_idx, value_sec)
StreetTurned(hand_id, new_street, community_cards)
SeatFolded(hand_id, seat_idx, was_auto: bool)
SeatAllIn(hand_id, seat_idx, final_stack: 0)
SeatLeftMidHand(hand_id, seat_idx)  # 罕见
ShowdownRevealed(hand_id, seat_idx, hole_cards)
HandEnded(hand_id, pot_winners, pot_distribution, rake)
```

**关键设计**:**事件是过去时态,不可变,append-only**。
- ❌ 不要 `update_action_event(...)` 这种 API
- ✅ 只能 `append_event(...)`
- ✅ State 永远从 events 重建

---

## §4. Invariants(跨 aggregate 一致性,自动 enforce)

```python
# Hand Aggregate 的不变量(任何 event 应用前检查):
1. len(active_seats) + len(folded_seats) + len(all_in_seats) = len(occupied_seats)
   # active + folded + all-in 之和 = 有人坐的座位数

2. at_most_one(seat.state == 'acting' for seat in seats)
   # 任意时刻只有 1 个 seat 在思考(timer 亮)

3. pot_size_current = sum(seat.total_bet_this_hand for seat in seats) - rake
   # 守恒律

4. if street == flop: len(community_cards) == 3
   # 街跟 community count 匹配

5. seat 状态转移合法性:
   - folded → folded ❌(死人复活违反)
   - all_in → 任何 active 行动 ❌

6. 行动顺序:PlayerActed.seat_idx 必须等于当前 pointer
   # 否则违反德州行动顺序
```

**违反 invariant 怎么办**?
- 🟢 阻塞:reject event + emit diag `invariant.violation`
- 🟡 警告:accept + log + emit diag
- 🔴 致命:abort hand,enter abandoned state

→ **P1/P2/P3/P4 cross-validation 收编到这里**。

---

## §5. Projections(read-side,从 event log 计算)

**Projection** = "实时订阅 event 流,维护一个 read 模型"。

```
PlayerStatsProjection(per player)
  ├── 触发:PlayerActed event
  ├── 更新:vpip / pfr / af / hands_played
  └── 物化:t_player_stats(SQL view 或 cache)

HandSummaryProjection(per hand)
  ├── 触发:HandEnded event
  ├── 计算:final_winner / key_actions / pot_distribution
  └── 物化:t_hand_summary

PositionMatrixProjection(per player)
  ├── 触发:PlayerActed event
  ├── 更新:VPIP / PFR per position(已有 view v_player_position_matrix)
  └── 物化:t_position_matrix

NetWinningsProjection(per player per session)
  ├── 触发:HandEnded event
  ├── 计算:net_bb 累积
  └── 物化:t_net_winnings(已有 view v_player_net_winnings)
```

**Path B 玩家画像 = 多个 projection 组合**。

**Path C dashboard** 直接读 projection 物化表,**不算逻辑**。

**Path D HUD overlay** 订阅 projection 更新 → 推 overlay。

---

## §6. Anti-corruption Layer(ACL — 防腐层)

**作用**:Pipeline 抓到的"raw 信号"(OCR text / phash / brightness)**不能直接进 domain**,必须经过翻译。

```
Pipeline (OCR/CNN) → Raw Signals → ACL → Domain Events → Aggregate
```

**ACL 职责**:
- 把 "fold_area OCR 出 '弃牌'" 翻译成 `SeatFolded(seat_X)`
- 把 "pot_size OCR 从 49 → 19" + community count 0 → 翻译成 `StreetTurned + HandEnded`
- 把 "phash A 跟 phash B hamming ≤ 3" 翻译成 `PlayerIdentified(seat, player_id)`
- **trust ladder 在 ACL 这一层做**:低桩信号不产生 Domain Event,只记 raw_data evidence
- **避免 domain 模型污染**

→ `events/normalizer.py` 是这一层的雏形。**Path B 起强化**。

---

## §7. Path B 落地路径(具体)

| 阶段 | 内容 | 工程量 |
|:---|:---|:---:|
| **B.1** | ACL 重构:把 OCR/CNN 信号翻译成 Domain Events(events/ 模块)| 1 周 |
| **B.2** | Hand Aggregate 实现 + Event Log persistence(可继续用 PG)| 1 周 |
| **B.3** | Player Aggregate + Identity Reconciliation service | 半周 |
| **B.4** | PlayerStatsProjection + dashboard 接入 | 半周 |
| **B.5** | 灰度并跑:Path A 主路径 + Path B 平行,数据 diff | 1 周 |

**总计**:**~3-4 周** 完整 Path B。

**关键决策**:Path A 老 action_events 表**保留**,Path B Event Log 用**新表**(不冲突,平行)。

---

## §8. 不应该做的事(避坑清单)

| ❌ 反模式 | 为什么不行 |
|:---|:---|
| 在 aggregate 里直接 SQL 查询 | 破坏 ACL,aggregate 必须只通过 repository 拿数据 |
| 多个 aggregate 互相调对方的内部方法 | 应该通过 Domain Event 通信 |
| 试图 update event log 里的旧 event | event 永不可变,只能 append 修正事件 |
| 把 pipeline 信号(phash / OCR result)塞进 Domain Event | 那是 raw signal,ACL 翻译完才是 domain 概念 |
| Path A 跟 Path B 共用同一张 action_events 表 | 数据结构会互相妥协 |

---

## §9. 跟现有 Path A 的关系(并存策略)

| 数据 | Path A | Path B |
|:---|:---|:---|
| action_events | 保留(老 schema)| 不用 |
| **event_log** 新表 | 不写 | 主存储 |
| 主表 stats(view)| `v_player_position_matrix` 等 | **新 projection 表**(`t_player_stats_v2`)|
| Dashboard 显示 | 优先 Path B projection,fallback Path A view | |

**两套共存** ≠ 数据矛盾,因为 Path B Event Log 跟 Path A 信号本来就来自同一 OCR/CNN,**只是组织方式不同**。

---

## §10. 触发 #LR15 全栈重构 的信号

满足以下任一即可启动 #LR15(把 Path A 收编进 DDD):

- MVP 完整 ship(Path A + B + C + D 都上线)
- Path B 跑了 1-2 个月,**Path A 老代码继续 patch 的频次显著高于 Path B**
- 新增功能时**频繁感到"这跟之前 X 重复"**
- 团队人数超过 1 人(共享心智模型成本)

**永不优先级**:MVP 主线之前永不优先。

---

## §11. 验证 / 质量

DDD 引入后**测试策略**:

```
单元测试(per aggregate):
  Hand Aggregate
    ✓ apply event → state 正确变化
    ✓ invariant 检查
  Player Aggregate
    ✓ identity reconciliation 规则
  Seat Entity
    ✓ 状态机转移合法性

集成测试:
  ✓ Pipeline → ACL → Events → Aggregate → Projection
  ✓ 一手完整 replay 一致性

回放测试(强):
  ✓ 从 raw event log 复盘任意一手,state 100% 重建
```

→ **复现率 50% → 接近 100% 的根本支撑**就是 event sourcing 的 replay 能力。

---

## §12. 索引

| 关联文件 | 关系 |
|:---|:---|
| `[[long-term-roadmap]] #LR15` | 全栈重构触发条件 |
| `[[dev-rule-validate-blind-spots]]` | DDD 设计也属盲点验证范畴 |
| 主题-数据质量.md | 跨验证机制对照 |
| 主题-产品形态.md | Path B/C/D 跟 DDD 衔接 |
| `events/normalizer.py` | 现有 ACL 雏形 |
| `pipeline/orchestrator.py` `_pointer_state` | 现有 Seat 状态机雏形(T48)|

---

## 总结(给未来的我们看)

**2026-05-29 这一天**,用户在闲聊中把"扑克 = 状态机"这个领域真相点醒,**而我从第 1 天起就没用过 DDD 思维**。

这个 doc 是**为了 Path B 不再犯 Path A 的"信号驱动 + 加规则 patch"老毛病**而立的。

如果 Path B 用了,Path C/D/#LR4/#LR5/#LR7 都会受益。
如果 Path B 没用,**至少这个 doc 在 #LR15 触发时给一份草图,不用从头想**。

**核心信条**(回 §1):**让代码结构匹配领域结构**。
