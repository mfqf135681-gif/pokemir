# 主题 — DDD 架构(Hand / Player / Seat Aggregates + Event Sourcing)

> **V2 — 2026-05-29 重写**(V1 同日,2 小时后被用户要求"全面审慎复检",查出 17 处错误,V2 修正)
>
> ⚠️ **V1 17 处错误 + 修正记录见 §15**,**Path B 启动者必读**避免重蹈覆辙。
>
> **作用**:Path B 启用 DDD 时的设计指导。
>
> **范围**:
> - ✅ Path B / Path C / Path D 新代码遵守
> - ✅ #LR4 / #LR5 / #LR7 等 LR 实现时参考
> - ❌ Path A 已完成代码**不重构**(沉没成本)
>
> **关联**:`[[long-term-roadmap]] #LR15` · `[[dev-rule-validate-blind-spots]]`

---

## §1. 设计哲学:领域驱动 而非 信号驱动 (诚实版)

### 1.1 核心信条

**让代码结构匹配领域结构**。

### 1.2 跟"信号驱动"的对比(诚实版)

| 维度 | Path A(信号驱动) | Path B(领域驱动) |
|:---|:---|:---|
| 抽象起点 | OCR / phash / brightness 抓什么,代码处理什么 | 扑克本身是什么,代码就是什么 |
| 加新 sensor | 加规则处理新信号 | ACL 翻译成已有 Domain Event |
| 加新 stat | 加 SQL view | 加 Projection 订阅 event log |
| 修 bug | 加 if-then 守卫 | 修 invariant 一处搞定 |
| 起跑成本 | **低**(直接写)| **高**(先建模 + 基础设施)|
| 长期维护 | 越来越重 | 越来越轻 |

### 1.3 DDD **不是银弹**(V2 新增坦白)

DDD 不擅长:
- 🚫 解决 sensor 漏抓问题(Pipeline OCR 漏 50% → ACL 翻译 50% events → state 50% 缺)
- 🚫 解决性能瓶颈(event sourcing 反而带来存储增长 + replay 成本)
- 🚫 没经验团队第一次落地时省事(实际比预期慢 2-3 倍)
- 🚫 自动减少代码总行数(基础设施增加抵消业务代码减少,**净行数可能 +5% ~ -10%**)

DDD 真正擅长:
- ✅ **降低长期心智负担**(领域逻辑统一,新人上手快)
- ✅ **降低 bug 类别**(invariants 自动 enforce,死代码不可能出现)
- ✅ **加速新功能**(LR4 rake / LR5 HUD / LR7 LLM 都受益)
- ✅ **提升可测性**(纯函数式 replay)

→ **选 DDD 的真理由是"心智 + 维护",不是"代码减半"**。

---

## §2. Bounded Contexts(2 个,V2 新增 — 修 V1 错误 #3)

观察者 vs 玩家是**两套不同 bounded context**,**不应该硬塞同一个 model**。

### 2.1 Observer Context

**主体**:用户在桌外旁观,所有 seat 都是对手。

**特点**:
- 无 hero(没有"我")
- 无 HoleCardsDealt event(看不见任何人底牌,除非摊牌)
- 无 hero_action 事件
- pot_size 通过 OCR 推断
- "总底池" 文字是 hand-end 信号(V1 仍适用)

### 2.2 Seated Context

**主体**:用户坐下打牌,seat_0 是 hero。

**特点**:
- 有 HoleCardsDealt event(hero 自己的牌)
- hero_action 来自客户端 UI 事件(不只 OCR)
- pot_size_current 跟 hero 行动可关联
- showdown 时其他玩家牌也可见

### 2.3 共享内核(Shared Kernel)

| 共享概念 | 备注 |
|:---|:---|
| Player Aggregate | 同一个玩家在两 context 都是同一个 |
| Hand Aggregate 主框架 | 一手扑克的状态机一致 |
| Domain Events 共享部分 | PlayerActed / SeatFolded / 等 |
| Anti-corruption Layer | 同样的 raw signal → 同样的 domain event |

**关键决策**:**Observer 先做(Path B 主线),Seated 后做**(seated 涉及 hero seat 特殊处理,工程量大,放后)。

---

## §3. 核心 Aggregates(V2 重设计 — 修 V1 错误 #1/#2/#4)

### 3.1 Aggregate 清单(原本只 3 个,V2 加到 5 个)

| Aggregate | 责任 | 持久化策略 |
|:---|:---|:---|
| **Session**(V2 新增) | 用户启 pipeline 到 Ctrl+C 的整段录制 | 1 row per session |
| **Table**(V2 新增) | 1 张桌的整段持续状态 | 1 row per table,持续时间内 |
| **Hand**(主) | 一手扑克的完整 lifecycle | event sourcing,event log |
| **Player** | 跨 hand / 跨 session 持久身份 | 1 row per player,projection 单独存 |
| **Seat**(per hand 子实体) | 1 个 seat 1 手中的状态机 | 不持久,从 Hand event log 重建 |

### 3.2 Hand Aggregate(V2 — 修 V1 漏 side pot / 时间语义)

```
Hand
├── id (UUID)
├── table_id (ref Table)
├── num_seats
├── button_seat
├── community_cards (current)
├── pots:
│   ├── main_pot (实时)
│   ├── side_pots: [SidePot...] (多人 all-in 时有多个,V2 修复 V1 漏)
│   └── total_pot_at_each_street: dict[Street, float] (历史)
├── street: enum {preflop, flop, turn, river, showdown, ended}
├── active_pointer: seat_idx | None
├── seats: dict[seat_idx, Seat Entity]
├── event_log: [DomainEvent...] (主存储)
└── status: enum {in_progress, completed, abandoned}
```

**重要决策**:Hand state = `replay(event_log)`。**不存 mutable state**。

### 3.3 Player Aggregate(V2 — projection 移出去,见 §6)

```
Player
├── id (UUID)
├── identity:
│   ├── primary_phash (64-bit)
│   ├── label (nickname,可选,人类可读)
│   ├── alias_phashes: [old_hashes]  (玩家换头像)
│   └── alias_nicknames: [old_nicks] (OCR 漂移)
└── reconciliation_history: [merge_events]

❌ V1 错误(已修):stats_projection 在 Aggregate 内部
✅ V2 正确:stats 是独立 PlayerStatsProjection,只订阅 event log,不在 Player Aggregate 里
```

### 3.4 Seat Entity(V2 — 修 V1 漏 sitting_out 状态)

```
Seat (per hand,Hand Aggregate 子实体)
├── seat_idx (0..n-1)
├── player_ref (Player.id | None)
├── state: enum
│   ├── empty           (无人坐)
│   ├── sitting_in      (已就座,本手未行动)
│   ├── sitting_out     (V2 新增:已就座但本手 preset 自动 fold)
│   ├── acting          (timer 亮起,在思考)
│   ├── folded          (主动弃牌,本手剩余 ticks 不再行动)
│   ├── auto_folded     (V2 新增:离桌/preset/超时自动 fold)
│   ├── all_in          (全押)
│   └── waiting         (preflop:posted blinds 但还没行动到他)
├── stack_initial / stack_current
├── street_actions: [Action...]
└── total_bet_this_hand
```

### 3.5 Table Aggregate(V2 新增)

```
Table
├── id (UUID)
├── name (e.g., "WePoker_PartyPoker_8seat_room123")
├── max_seats: int
├── blind_levels: [(level, sb, bb, ante)] (tournament 时变化)
├── current_blind_level (index)
├── seat_occupancy: dict[seat_idx, Player.id | None] (跨 hand 持续)
└── hands: [Hand.id...]  (有序)
```

### 3.6 Session Aggregate(V2 新增)

```
Session
├── id (UUID)
├── started_at / ended_at
├── pipeline_version / profile_name
├── tables_observed: [Table.id...]
└── hands_recorded: [Hand.id...]
```

---

## §4. Domain Events 清单(V2 修订 — 修 V1 错误 #5/#6/#7)

### 4.1 共 17 个 Domain Events(V1 是 11 个 + 漏 5 个 + 错 1 个)

**Session / Table 层(新增):**
1. `SessionStarted(session_id, pipeline_version, started_at)`
2. `SessionEnded(session_id, ended_at, reason)`
3. `TableObserved(table_id, name, max_seats, observed_at)`
4. `PlayerJoinedTable(table_id, player_id, seat_idx, joined_at)`(V2 新增)
5. `PlayerLeftTable(table_id, player_id, seat_idx, left_at)`(V2 新增)
6. `BlindLevelChanged(table_id, new_level, sb, bb, ante)`(V2 新增)

**Hand 层:**
7. `HandStarted(hand_id, table_id, button_seat, players_at_seats, started_at)`
8. `ButtonMoved(hand_id, from_seat, to_seat)`(V2 新增,可能 = HandStarted 子事件)
9. `BlindsPosted(hand_id, sb_seat, sb_amount, bb_seat, bb_amount)`
10. `HoleCardsDealt(hand_id, cards)` — **仅 Seated context**
11. `PlayerActed(hand_id, seat_idx, action_type, amount, street)`
12. `StreetTurned(hand_id, new_street, community_cards_added)`
13. `SeatFolded(hand_id, seat_idx)` — V2 移除 `was_auto` bool(domain 不应知道 UI 机制)
14. `SeatSatOut(hand_id, seat_idx)`(V2 新增,区别于 SeatFolded)
15. `SeatAllIn(hand_id, seat_idx, final_stack=0)`
16. `ShowdownRevealed(hand_id, seat_idx, hole_cards)`
17. `HandEnded(hand_id, winners, pot_distribution)` — V2 移除 `rake`(rake 由 projection 计算,不是 observed)

### 4.2 移除的 V1 "伪 domain events"(V2 — 修错误 #5)

| 移除 | 原因 |
|:---|:---|
| `TimerObserved` | Timer 是 UI 机制,**不是扑克概念**;ACL 用它产生 PlayerActed,但它本身不进 domain |
| `SeatLeftMidHand` | 不存在(玩家中途离开 = SeatSatOut + auto-fold)|

### 4.3 Event 三大基础设施(V2 新增 — 修错误 #7)

#### Causal Ordering(因果序)

每 event 必须有:
```
event_id: UUID
session_id: UUID
hand_id: UUID | None
sequence_in_hand: int (1-based,同 hand 内有序)
observed_at: timestamp (Pipeline 检测时间)
inferred_domain_at: timestamp | None (推算的真实领域时间)
caused_by: event_id | None (因果链)
```

**关键区别**(V2 新增):**Observation Time** (Pipeline tick time) vs **Domain Time**(player 实际行动那一刻),**两者可能差 100ms-2s**。

#### Idempotency(幂等)

每 event 写入 event_log 前查 (session_id, hand_id, sequence_in_hand) 是否已存在。已存在 → reject,不重写。

**意义**:Pipeline 重启 / 重连不会污染 event log。

#### Versioning(版本)

每 event schema 加 `event_version: int`。
Schema 演进时:
- 老 event 保留原 version
- 加 migration projection,**只读时翻译,不改 event_log**

→ event_log 永远 append-only,**永不重写**。

---

## §5. Invariants(V2 — 标时间作用域,修 V1 错误 #8/#9/#10)

### 5.1 时间作用域分类

| 作用域 | 何时检查 |
|:---|:---|
| **任意 tick** | 每 event 写入前检查 |
| **每 street 结束** | StreetTurned 时检查 |
| **每 hand 结束** | HandEnded 时检查 |

### 5.2 Invariants 详单

| # | Invariant | 作用域 | 违反时 |
|:---|:---|:---|:---|
| 1 | `len(seats by state in [acting, sitting_in, waiting]) ≤ active_count` | 任意 tick | reject event + emit diag |
| 2 | `at_most_one(seat.state == 'acting')` | 任意 tick | reject + diag |
| 3 | `pot_main = sum(active_seat.total_bet) - rake_estimate` | 每 hand 结束 | warn,不 reject |
| 4 | `street == flop → len(community_cards) >= 3` | StreetTurned 后 | reject + 触发 reconcile |
| 5 | `PlayerActed.seat_idx == active_pointer` | 任意 tick | reject(德州顺序违反)|
| 6 | `folded / auto_folded / all_in / sitting_out seat 不能产生 PlayerActed` | 任意 tick | reject(死人复活违反)|
| 7 | `Side pot 总额 = sum(all_in seat 余额) + active_seat 跟注` | 每 street 结束 | warn |

### 5.3 V1 漏的 sit_out / side pot 处理(V2 修)

- **sit_out**:`sitting_out` 是独立状态,既不算 active 也不算 folded。Invariant 1 包含 sitting_out。
- **side pots**:Hand.pots.side_pots 是 list,multi all-in 时多个;invariant 7 跟踪每个 side pot。

---

## §6. Projections(V2 独立章节 — 修 V1 错误 #1)

**Projection** = "订阅 event 流维护 read 模型"。**独立于 Aggregate**。

### 6.1 核心 Projections

#### PlayerStatsProjection

```
触发:PlayerActed event
订阅:Player Aggregate 的所有 PlayerActed
更新:
  - hands_played++
  - vpip_by_position[pos] += (call/raise ? 1 : 0)
  - pfr_by_position[pos] += (raise ? 1 : 0)
  - af_by_street[street] += weighted aggressive ratio
物化:t_player_stats(PG 表)
```

#### HandSummaryProjection

```
触发:HandEnded event
计算:winner / key_actions / pot_distribution / rake_inferred
物化:t_hand_summary
```

#### NetWinningsProjection

```
触发:HandEnded event 中的 pot_distribution
累加:player.net_bb += distribution_share
物化:t_net_winnings(类似 v_player_net_winnings 但实时)
```

#### PositionMatrixProjection

```
触发:PlayerActed event
按 (player, position, street) 累加
物化:t_position_matrix(类似 v_player_position_matrix)
```

### 6.2 Projection 一致性

| 模式 | 优 | 缺 |
|:---|:---|:---|
| Real-time(每 event 触发更新)| 数据新 | 慢,容易冲突 |
| Batch(定时 replay event_log)| 简单 | 数据滞后 |
| Hybrid(实时 + 定时 reconcile) | 折衷 | 复杂 |

**Path B 选**:**Batch + 增量**(每 5 分钟从最新 event id 增量 replay,Path B 主线不卡)。

### 6.3 Snapshot(V2 新增 — 修 V1 漏)

Event log 增长后 replay 慢。每 100 events 存 snapshot:
- Hand:snapshot 在 HandEnded 后
- Player Aggregate:snapshot 在 hands_played 整 100 倍数时

Replay 从最近 snapshot 开始,不从头。

---

## §7. Anti-corruption Layer(V2 — 修 V1 错误 #12/#13)

### 7.1 ACL 职责(precise 版)

**单向(主路径)**:Pipeline Raw Signals → ACL → Domain Events
- 输入:`OCR_text="跟注" + amount=100 + stack_delta=-100`
- 输出:`PlayerActed(seat=X, action=CALL, amount=100)`

**双向(V2 修)**:**Domain → Pipeline 反馈**也存在
- Domain 知道 `seat X is folded` → Pipeline 通过 ACL 收到"下 tick 别 OCR seat X 的 action_area"
- 这是当前 T46-A guard 的 DDD 化

### 7.2 ACL 不做什么(V2 修 V1 错位)

❌ ACL **不做** trust ladder 过滤(V1 我说在 ACL,**错了**)
- ACL 接收 raw signal,**没法判断 confidence**
- Trust ladder 是 **Domain Service**(我叫它 `EventFilter`)做的事
- 流程:Raw Signal → ACL → Candidate Event → EventFilter(查 confidence + 历史)→ Final Domain Event

### 7.3 ACL 实例(V2 新增)

```
ACL Rules(示例):
  "fold_area OCR == 弃牌" → SeatFolded(seat_X)
  "stack_delta == 0 AND timer 已消失 AND action_text 含 'check/过牌'" → PlayerActed(CALL/CHECK)
  "phash hamming ≤ 6 with registered" → PlayerIdentified(seat, player_id)
  "community_count 5→0 OR pot_drop > 50%" → HandEnded + HandStarted
```

→ `events/normalizer.py` 是 ACL 的雏形,Path B 强化。

---

## §8. 持久化策略(V2 新增 — 修 V1 完全没提)

### 8.1 存储结构

```
PG schema(Path B 新表):
  t_event_log
    id (sequence_id, append-only PK)
    session_id / hand_id / event_id
    event_type / event_version
    sequence_in_hand
    payload (JSONB)
    observed_at / inferred_domain_at
    
  t_player_aggregate
    player_id PK
    primary_phash / label
    alias_phashes JSONB
    
  t_table_aggregate
    table_id PK
    name / max_seats / blind_levels JSONB
    
  t_hand_snapshot(每手结束 snapshot)
    hand_id PK
    final_state JSONB
    
  Projections (read 模型):
    t_player_stats / t_hand_summary / t_net_winnings / t_position_matrix
```

### 8.2 Path A 表 vs Path B 表

| Path A 旧表 | Path B 新表 | 关系 |
|:---|:---|:---|
| `hands` | `t_hand_snapshot` | 灰度期共存,V1 数据 import 进 V2 用 migration script |
| `action_events` | `t_event_log`(子集)| Path A action_events 是"扁平 row",Path B event_log 是"事件流" |
| `diagnostic_events` | 保留,共用 | 都需要诊断 |
| `v_*` views | `t_*` projection 表 | view 是计算,projection 是物化 |

### 8.3 Migration 策略(V2 新增)

1. **冷数据**(Path A action_events)→ 一次性翻译成 Path B event_log,保留 Path A 表只读
2. **热数据**(Path B 启动后)→ Path A + Path B 双写,跑 1-2 周
3. **数据 diff** 验证一致 → 切 Path B 主路径,Path A 表降级 read-only
4. **冷却 1 个月** → Path A 表归档

---

## §9. 测试策略(V2 修 — 修 V1 错误 #11 核心 logic 反了)

### 9.1 测试金字塔

```
单元测试(per Aggregate):
  Hand
    ✓ apply event → state 正确
    ✓ invariants 检查
  Player
    ✓ identity reconciliation
  Seat
    ✓ 状态机转移合法性
集成测试:
  ✓ Pipeline → ACL → EventFilter → Event Log
  ✓ Event Log → Projection → Read 模型
回放测试:
  ✓ 从 event log 重建 state 一致
  ⚠️ **不等于"跟现实一致"**(见 §9.2)
```

### 9.2 ⚠️ 回放测试的根本限制(V2 修 V1 关键错误)

**V1 错误**:我隐含说"Replay → state 准确"。

**V2 修正**:
- Pipeline 漏抓 50% actions → ACL 产生 50% events → event_log 缺 50% events
- Replay only "consistent with what we observed",**not "consistent with reality"**
- → DDD 不解决 sensor bottleneck

→ **Replay 测试只能验证 deterministic 性(同 event log replay 多次 state 一致),不能验证完整性**。

### 9.3 完整性需要新维度

DDD 之外需要额外指标:
- **Action capture rate**:Pipeline 抓到 / 真发生(无法 ground truth,只能跟随 timer 推断)
- **Pointer accuracy rate**:T48 v3 灰度期跑出来
- **Conservation rate**:invariant 3 (pot 守恒)成立的 hand 比例

---

## §10. Path B 落地路径(V2 — 修 V1 错误 #14 时间估算)

### 10.1 真实时间表(V2 — 8-14 周)

| 阶段 | 内容 | V1 我估 | V2 修正(0 DDD 经验)|
|:---|:---|:---:|:---:|
| B.1 ACL 重构 + EventFilter | events/ 模块 + trust ladder 迁移 | 1 周 | **2-3 周** |
| B.2 Event Log + 3 个核心 Aggregates | 设计 + impl + repository | 1 周 | **2-3 周** |
| B.3 Player Aggregate + Identity Service | 跨 session merge 逻辑 | 半周 | **1-2 周** |
| B.4 4 个 Projections | 物化 + 接 dashboard | 半周 | **1-2 周** |
| B.5 灰度并跑 + Migration | 数据 diff + 切换 | 1 周 | **2-4 周** |
| **总计** | | **4 周** | **8-14 周** |

### 10.2 灰度并跑 metric(V2 新增 — 修 V1 错误 #15)

| Metric | 目标 |
|:---|:---|
| Path B event 产出量 / Path A action 产出量 | ≥ 95%(漏 ≤ 5%)|
| Path B PlayerStats vs Path A view | per-player VPIP 差 ≤ 2pp |
| Path B HandSummary 守恒律达成率 | ≥ 95% |
| Path B 写入延迟 / Path A | ≤ 1.5x |
| 跨 Path 数据差异 SQL | 自动跑 + 日报 |

**任一不达标 → 不切换 main**。

### 10.3 切换决策树

```
跑灰度 2-4 周
  ↓
Metric 达标? 
  Yes → 切 Path B 为主路径,Path A read-only
  No  → 修 / 加 Migration / 加 Adapter,继续灰度
```

---

## §11. Path A 共存策略(V2 — 修 V1 错误"两套不矛盾"的乐观假设)

### 11.1 风险:Path A 跟 Path B 必然短期不一致

**真实情况**:
- 同 hand,Path A action_events 可能有 6 行,Path B event_log 可能有 8 个事件
- 不是因为 Path B 抓得多,而是 ACL 产生中间事件(如 StreetTurned)
- Path A 的 confidence_score 0.5 行,Path B EventFilter 可能 reject 该 event

### 11.2 Dashboard 显示策略(V2 修)

**V1**:"Dashboard 优先 Path B projection,fallback Path A view" — **危险,混合真相源**

**V2**:Dashboard 在灰度期**只显示 Path A** OR **只显示 Path B**(用户切换),**绝不混合**。

切完 main 后 Path A view 不再喂 dashboard。

---

## §12. #LR15 全栈触发条件(V2 — 修 V1 错误 #16 不可测)

### 12.1 量化触发指标(任一)

| 指标 | 阈值 |
|:---|:---|
| Path A 月度 patch 数 / Path B 月度 patch 数 | ≥ 2x |
| Path A 加新功能平均工程量 / Path B | ≥ 1.5x |
| Path A 月度 bug fix 工时 | ≥ 16 小时/月 |
| 团队人数 | ≥ 2(共享心智成本)|

### 12.2 非量化(辅助)

- 用户主动说"Path A 这边又踩同款坑了"超过 3 次
- Path A 触发 dev-rule-validate-blind-spots 警告频次升高

---

## §13. 已知限制 / 不解决的问题(V2 新增)

| 问题 | DDD 不能解 | 替代方案 |
|:---|:---|:---|
| Pipeline 漏抓 50% action | 是 | T48 指针架构 / #LR1 VLM |
| OCR 字符漂移 | 是 | dHash + alias_history |
| 多桌同时观战 | 半 | Table aggregate 已支持,但 Pipeline 单 profile 跑不到 |
| 实时延迟 < 100ms | 是 | Pipeline 优化 + 异步 OCR |
| Hero hole cards 隐私 | 不涉及 | seated mode 独立 ACL |
| 边池复杂逻辑 | 半 | Hand.pots.side_pots 支持,但实现复杂 |

---

## §14. 反模式避坑(V1 保留 + V2 补)

| ❌ 反模式 | 原因 |
|:---|:---|
| Projection 写在 Aggregate 内 | V1 我犯过,**违反 read-write 分离** |
| TimerObserved 当 Domain Event | UI 信号污染 domain |
| Trust ladder 放 ACL | ACL 无 confidence 信息,放错层 |
| Aggregate 直接 SQL 查询 | 破坏 repository pattern |
| Event 可变 | event sourcing 灵魂违反 |
| Dashboard 混合 Path A + Path B 真相 | 用户困惑,数据可信度下降 |
| 单 BC 塞 observer + seated | 状态 / event 不同,硬塞失败 |
| 没 idempotency 写 event_log | Pipeline 重启污染历史 |
| 没 snapshot 直接 replay all | 性能瓶颈 |

---

## §15. ⚠️ V1 错误标注(透明记录,避免重蹈)

### 15.1 背景

V1 于 2026-05-29 我快速写完(半天),没经过审慎检视。同日用户要求"全面审慎复检",查出 17 处错误,V2 修正。

**这一节是 transparency record,让未来的我们(以及 #LR15 全栈实施者)知道哪些"看起来工整的 DDD 草图"其实是错的,不要重蹈**。

### 15.2 17 处 V1 错误清单

| # | V1 错误 | 严重度 | V2 修正位置 |
|:---|:---|:---|:---|
| **1** | Projection 放在 Player Aggregate 内 | 🔴 P1 | §3.3 / §6 |
| **2** | 漏 Table / Session Aggregate | 🔴 P1 | §3.5 / §3.6 |
| **3** | Observer / Seated 没分 bounded context | 🔴 P1 | §2 |
| **4** | Player 跨 session 边界含糊 | 🔴 P1 | §3.3 + Identity Service |
| **5** | TimerObserved 当 Domain Event | 🔴 P1 | §4.2 移除 |
| **6** | 漏 5 个 events(PlayerJoined/Left/ButtonMoved/PlayerSatOut/BlindLevelChanged)| 🔴 P1 | §4.1 |
| **7** | Causal Order / Idempotency / Schema Versioning 全无 | 🔴 P1 | §4.3 |
| **8** | Invariants 不标时间作用域 | 🟠 P2 | §5.1 |
| **9** | 漏 sitting_out 状态 | 🟠 P2 | §3.4 + §5.2 |
| **10** | 漏 side pot 建模 | 🟠 P2 | §3.2 / §5.2 |
| **11** | Replay 测试错置"=真实一致" | 🔴 P1 | §9.2 |
| **12** | Trust ladder 错放 ACL | 🟡 P3 | §7.2 |
| **13** | ACL 单向假设 | 🟡 P3 | §7.1 |
| **14** | Path B 时间估 4 周(应 8-14)| 🟡 P3 | §10.1 |
| **15** | 灰度 metric 没定 | 🟡 P3 | §10.2 |
| **16** | #LR15 触发条件不可量化 | 🟡 P3 | §12 |
| **17** | 成本收益误导(说代码 -40%)| 🟡 P3 | §1.3 |

### 15.3 V1 → V2 的核心教训

| 维度 | V1 | V2 教训 |
|:---|:---|:---|
| 设计速度 | 半天写完 | DDD 设计**需要至少 2-3 天 + 多轮自审** |
| 心态 | "结构化模板 = 有思考" | 表面工整 ≠ 实质正确,**易陷"AI 表达 bias"** |
| 自检 | 无 | 必须经"全面审慎复检"才能 ship |
| 估算 | 优化太多 | DDD 第一次落地 **2-3x 慢于熟练团队** |
| 售卖点 | 代码减半 | 真理由是心智 + 维护,**不是代码行数** |

### 15.4 给未来读者的话

如果你是 #LR15 启动时来读这份 doc 的人(可能是另一个 AI,也可能是更熟练的我):

**请把 §15 当镜子用**:
- "我现在写的章节是不是也犯了 V1 同款病?"
- "我有没有把 Projection 又塞进 Aggregate?"
- "我有没有低估时间?"
- "我有没有把 UI 信号当 Domain Event?"

**每写完 1 节,自问 1 次 V1 17 错误清单**。这是从血淋淋的实践得来的 checklist。

---

## §16. 索引

| 关联 | 关系 |
|:---|:---|
| `[[long-term-roadmap]] #LR15` | 全栈重构触发条件 |
| `[[dev-rule-validate-blind-spots]]` | DDD 设计也是盲点验证 |
| `[[dev-rule-mode-drift-guard]]` | 设计 doc 也要 §11 模式守卫 |
| 主题-数据质量.md | 守恒律 / 跨验证 |
| 主题-产品形态.md | Path B/C/D 接入 |
| `events/normalizer.py` | ACL 雏形 |
| `pipeline/orchestrator.py` `_pointer_state` | Seat state machine 雏形 (T48) |
| T48 v3 | 指针架构 stage 1 已实施 |

---

## 总结(V2 — 诚实版)

**这是 V2,V1 我半天写完结果 17 处错**。

**真相**:
1. DDD 不是银弹,**不解决 sensor 问题**
2. DDD 第一次落地 **8-14 周**,不是 4 周
3. **代码行数可能 +5% ~ -10%**,不是 -40%
4. **真理由是心智 + 维护性**,不是行数
5. 启动 Path B 前**必须再审一遍** §15 17 个错误清单
6. **§15 是这份 doc 最重要的章节**,不是 §2 那些工整的领域模型

**核心信条**(再回 §1):**让代码结构匹配领域结构** — 但**别 mistake 工整模板为正确实质**。
