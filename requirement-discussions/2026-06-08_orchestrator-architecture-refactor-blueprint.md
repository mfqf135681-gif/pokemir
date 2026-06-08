# 录制主程序(PipelineOrchestrator)架构重构蓝图

**状态:BLUEPRINT(蓝图,不实施)**。基于 2026-06-08 全量深读 50 个方法 + 与用户的设计共识。
**实施前置:识别层冻结 + 契约 #227 落地(见 §5 时序)。在此之前本文档只作蓝图,不动代码。**
关联:#238(拆 _process_seat_actions)· #226(求解器)· #227(契约)· [[constraint-solver-paradigm]] · [[pillar-confidence-doctrine]]
红线:Image-only 合规。本重构 **性能提升 ≈ 0**(已与用户确认,不以提速为由)。

---

## §1 现状全貌(任务一:全量深读结论)

`pipeline/orchestrator.py` = **3143 行、50 方法的上帝类 PipelineOrchestrator**。

**生命周期(单线程同步):**
```
__init__(150-309:建识别器/载模板/载phash参考/读桌规/建~20状态字段)
  → start(): while running: _tick(); sleep(250ms)
  → _shutdown()
_tick = 总指挥,顺序调 12 步;_start_new_hand / _end_current_hand 各自是子编排
```

**状态散在两对象、无归属规律(耦合根源):**
- `self._xxx`(~20):_btn_confirmed/_btn_pending/_last_button_seat、_active_set/_hand_dealt_seats/_seat_gone_ticks、_blinds_pending/_blinds_attempts、_table_blinds、_hand_win_seats/_hand_start_tick、_empty_refs/_action_phash/_zone_readers/_digit_reader、_fold_read_profile、_avatar_swap_count…
- `tracker._xxx`(~15):current_hand、_prev_stack、_street_to_call/_street_has_bet、_folded_seats/_empty_seats/_seats_with_events_this_hand、_last_action_at、_pending_decision_time/_timer_state/_used_timebank、player_id_map/_avatar_fingerprints、latest_pot_bb/_pot_before_tick/_hand_pot_peak、_idle_avatar_hash/_showdown_captured_this_hand/_seat_pred_history/_showdown_last_cnn_at、_position_map、_hero_seat_idx_cache、_last_roi_img/_last_roi_text/_roi_force_refresh_at…

**🔴 跨切面共享状态(模块切不干净的真因,务必看):**
| 状态 | 写者 | 读者 |
|---|---|---|
| `is_skippable_seat`(folded∪empty) | — | ~6 处(seat循环/pre_batch/showdown/blinds/forced) |
| `_folded_seats` | ActionInterpreter、_rescue_silent_fold | ShowdownCapturer、is_skippable、blinds |
| `_prev_stack` | ActionInterpreter(+continue路径)、blinds | ActionInterpreter(stack_before)、blinds、forced |
| `_idle_avatar_hash` | seat循环读fold_area顺带写、_initialize_avatar_baselines | ShowdownCapturer |
| `_street_to_call/_has_bet` | ActionInterpreter(本tick前座) | ActionInterpreter(本tick后座)= **同tick跨座依赖** |
| `latest_pot_bb/_pot_before_tick` | PotTracker | ActionInterpreter(pot_delta) |
| `player_id_map` | PlayerID | 几乎所有 event 创建处 |
| `button_seat_index` | HandSegmenter | blinds、blind_levels |

→ **任意方法可摸任意状态**;"每模块独占状态"是目标,**现实里这 8 个状态天生横跨多模块**(见 §5 修正)。

---

## §2 每方法/模块的重构判断(任务二)

判定:🟢 原样迁移(逻辑不改,只挪进模块)/ 🟡 轻度(拆分/小重构)/ 🔴 需重构(真纠缠,需分解)。

| 目标模块 | 方法 | 判定 | 理由 |
|---|---|---|---|
| **CaptureService** | refresh_frame*/capture_roi*/_get_tick_frame_grey/_recognize_only_roi/_capture_with_diff_trigger | 🟢 | 内聚、纯基建 |
| **Recognizers(已是独立类)** | _ocr_stack_chips/_reader_for/_stack_chips_recipe/_stack_text_to_chips | 🟢 | 薄包装,内聚 |
| **util** | _avg_hash_int/_hamming64/_slot_has_card/_hero_cards_present | 🟢 | 纯静态 |
| **HandSegmenter** | _scan_button_white_frac/_predict_next_button/_detect_button_position(+button_move_online已纯) | 🟢 | 自包含;状态仅 _btn_*/_last_button_seat |
| **PotTracker** | _process_pot | 🟡 | 清,但**内嵌"总底池"换手触发**(混了切手)→ 抽出归 Segmenter |
| **CommunityTracker** | _process_community_cards | 🟢 | 内聚 |
| **WinDetector(+xx)** | _scan_win_amount | 🟢 | 内聚;状态 _hand_win_seats |
| **SeatLifecycle** | _detect_empty_seats/_classify_occupancy/_detect_active_set | 🟢 | 内聚 |
| 同上(fold 救援) | _rescue_silent_fold | 🟡 | 跨界:写 _folded_seats + 合成 FOLD event(碰持久化) |
| **PlayerID** | _capture_player_ids | 🔴 | 150行:hash+swap去抖+缓存锁+指纹+OCR共识+TempUser+模糊别名 一坨 → 拆子步 |
| 同上 | _upgrade_tempuser_to_real/_canonicalize_player_id_map | 🟢 | DB-sync 助手,内聚 |
| **BlindsModule** | _detect_blind_levels/_read_table_blinds/_inject_forced_from_blinds/_inject_post_events/_emit_forced_event | 🟡 | 内聚但双路径(桌规/OCR);状态 _table_blinds/_blinds_pending |
| **TimerModule** | _process_timer/_finalize_timer | 🟢 | 小、内聚(喂 decision_time) |
| **ShowdownCapturer** | _detect_hero_seat_index/_try_capture_showdown_live/_capture_showdown_cards/_dump_showdown_crop/_initialize_avatar_baselines | 🟡 | 簇内聚;唯一外依赖 `_idle_avatar_hash` 由 seat循环写 → 来源要显式交接 |
| **ActionInterpreter ★** | _process_seat_actions | 🔴🔴 | 575行 observe+interpret+persist+7处状态突变 = 主战场(#238) |
| 编排里的 3-way 包 | _tick_active_set_and_blinds | 🔴 | 一方法捆了 活跃集 + silent-fold救援 + 盲注注入 三责 → 必拆 |
| **HandReconstructor(手末)** | _infer_insurance(+_end 内联 reconstruct_hand_chips) | 🟢 | reconstruct.py 已半解耦;保险纯逻辑 |
| 诊断探针 | _emit_silent_fold_profiles | 🟢(或删) | VERBOSE_DIAG 探针,调查已结;可删 |
| **Orchestrator(瘦)** | __init__/start/stop/_tick/_start_new_hand/_end_current_hand/_shutdown/_seat_position/_build_facing_action | 🟡 | __init__ 按模块拆 setup;_tick/_start/_end 留作"接线+生命周期",变薄 |

**结论:50 个里真需【分解】的只有 4 个**(_process_seat_actions、_tick_active_set_and_blinds、_capture_player_ids、_process_pot 抽切手触发);其余多是**原样迁移(🟢)或轻度拆分(🟡)**。所以这次本质是**模块切分(迁移+定状态归属)**,不是大改逻辑。

---

## §3 需重构部分的具体计划(任务三,只列 🔴/🟡)

### 3.1 ★ ActionInterpreter:拆 _process_seat_actions(#238)
**现状 29 步**:每座 跳过守卫 → 读stack → 读fold_area(弃牌/allin/timer/基线/探针) → 读action(phash/OCR) → 读amount → check_change → parse → 算delta → infer_action_from_delta → P3覆盖 → reconcile金额 → 建raw_data → compute_confidence → item2 → 7处状态突变 → 5s去重 → low_tier门 → 落库 → 复采artifact。
**拆法(三层)**:
- **observe_seat(seat) → SeatReads**{stack_now, action_text, amount, fold_text}:纯读像素(复用 CaptureService + Recognizers + pre_batch)。
- **interpret_seat(reads, state) → (event|None, state')**:把 算delta/infer/override/reconcile/confidence/item2 + 7处状态突变 收进一个**有状态 reducer**。⚠️ 含 `time.time()` 去重 → **注入 clock 才可纯测**(见 §5 修正⑥)。
- **persist(event)**:落库 + 低置信 artifact(artifact 复采像素的脏行为改为传入已抓图)。
- 验:reducer 喂合成 reads+state 断言 event+state' → **Linux 单测**(Win 关也能验)。

### 3.2 拆 _tick_active_set_and_blinds(3-way 包)
拆成 3 个独立调用,由 _tick 顺序编排:`SeatLifecycle.update_active_set()` → `SeatLifecycle.rescue_silent_folds()` → `BlindsModule.inject_if_pending()`。各自独立可测。

### 3.3 拆 _capture_player_ids(150行)
拆子步(同一 PlayerID 模块内):`_avatar_hash_and_swap()`(swap去抖)→ `_cache_lock_or_fingerprint()`(缓存锁+指纹匹配)→ `_ocr_consensus()`(双读)→ `_fuzzy_alias()`(别名归并)→ `_fallback_tempuser()`。逻辑不改,可读性+可测性升。

### 3.4 PotTracker 抽出内嵌切手触发
`_process_pot` 里的"总底池 label → end+start hand"(L2851)**移到 HandSegmenter**(它是切手职责,不该埋在读底池里)。PotTracker 只剩纯读底池 + 单调性护栏。

### 3.5 ShowdownCapturer 基线交接显式化
`_idle_avatar_hash` 现由 seat 循环读 fold_area 顺带写。拆模块后,**基线写入改由 SeatLifecycle 显式产出 → 传给 ShowdownCapturer**(不再隐式跨模块写)。

---

## §4 主程序优化蓝图(任务四:结合共识)

### 4.1 目标分层(一条数据对象穿过)
```
CaptureService → 抓帧/缓存/裁ROI/diff缓存
Observation    → 各识别器吐【原始观测】TickObservation{button,community,pot,seats:{stack,marker,fold,allin,timer,action,amount}}
HandState      → 单一所有者(清理后的 tracker),模块经【定义接口】读写,杜绝"谁都摸"
Segmenter/Blinds/SeatLifecycle/ActionInterpreter/Showdown/Win/Reconstructor
               → 消费 Observation + 读/改 HandState(经接口)
Persist        → 落库
Orchestrator   → 瘦接线 + 生命周期(_tick/_start/_end)
```
**关键缝:** ① TickObservation(观测与解释解耦)② HandState 单一所有者(治"状态散两处、谁都摸")③ ActionInterpreter reducer(解释与落库解耦)。

### 4.2 配置/模式存储方案(已与用户定稿)
- **一个自包含大 JSON / 每个(客户端 × 座型 × 观战/上桌)**:几何 + `num_seats` + `observer` 字段 + **全部 phash 参考**(card_marker_ref 已在内;再纳入 action_refs / empty_refs / digit_templates,与 card_marker 同模式:工具外科手术式更新各自 section,不碰几何)。
- **observer 折进 JSON**(现为 `--observer` flag,仅 1 处功能分叉 L1480 → 搬入零风险),loader 从 JSON 读 → **加载一个 JSON = 原子无损切模式**。
- **4 模式切换 = 选 profile → 带新 profile 重启 pipeline**(干净状态);dashboard 一键 = 写 profile 名 + 触发重启。
- **接受同客户端 4 模式间的重复**(用户拍板:重复必要、占空间小);**重复去重(base+overlay merge)留作逃生口,YAGNI 不预先上**。
- **性能影响:无**——所有参考 `__init__` 一次性载入内存,热路径只用内存结构,不读盘;profile 运行时只读不重写。

### 4.3 迁移顺序(每刀行为保持 + 可验)
1. **HandSegmenter**(最自包含、button_move_online 已纯)— 安全第一刀;
2. **PotTracker / CommunityTracker / WinDetector**(高内聚易迁);
3. **CaptureService**(基建抽出);
4. **ActionInterpreter reducer**(最值、灭怪兽,纯逻辑 Linux 单测;但先钉死跨座/跨tick状态契约 + 注入 clock);
5. **SeatLifecycle / PlayerID / Blinds**(拆 3-way 包、拆 150 行);
6. **ShowdownCapturer**(基线交接显式化);
7. **HandState 单一所有者** + **Orchestrator 收薄**(最后,因为它依赖前面模块的接口定型);
8. **存储整合 + observer 折进 JSON + 4 模式 schema**(可与 1-7 并行,因只动加载层)。

---

## §5 全面复审与修正(任务五:拆自己的台)

**站得住的:** 现状地图(50方法/14簇/8个跨切面状态)准确;分层方向(观测↔解释↔落库 + HandState 单一所有者)与 #226/#227 一致;"真需分解的只有 4 个"这个判断降低了想象中的工作量。

**必须修正/警示的:**
1. **🔴 时机与序(最重):** 本重构动的是**未冻结的识别承载层**。识别层接下来必动(#235-B、#239…)→ 现在拆好会返工。**铁律:重构排在「识别层冻结 + 契约 #227」之后** —— #227 把"观测接口"定死,那时拆才一次到位。
2. **🔴 "每模块独占状态"是愿景,非现实:** §1 那 8 个跨切面状态(_folded_seats / _prev_stack / _idle_avatar_hash / _street_* …)天生横跨 ≥3 模块。**所以 §4.1 的 HandState 必须是「单一所有者 + 定义接口」,不是"切碎给各模块"** —— 否则切不干净。这是分解的真难点。
3. **🔴 行为保持风险高 + 当前不可验:** 代码满是 diag / `mirror_seat_state` 影子写 / 5s墙钟去重 / `_prev_stack` 双处更新 / artifact 复采像素 等跨切面副作用,搬动易引入**编译/单测看不出、仅 live 暴露**的漂移。**Win 关着是最不能验的时候** —— 之前"Win 关着是纯逻辑重构好时机"是自我合理化,撤回。
4. **"自包含安全第一刀"部分修正:** ShowdownCapturer 的基线由 seat 循环写,非真自包含 → 拆它必须先做 §3.5 的交接;故 HandSegmenter 才是真第一刀。
5. **PotTracker 内嵌切手触发(§3.4)** 是个隐藏耦合,复审才显式拎出 —— 切手逻辑散在两处(button + 总底池),迁移时要合并到 Segmenter。
6. **"reducer 可 Linux 单测"打折:** 含 `time.time()` 墙钟去重 → 非纯函数,**须注入 clock** 才可测。
7. **存储整合的代价复核:** 大 JSON 的几何被机器 blob 埋深、git diff 变吵 —— 用户已接受(占空间小、重复必要);**性能影响经代码核实为 0**(载入期事件,非热路径)。

**复审结论:方向对、地图对、存储方案对;唯一被否的是"现在就重构"** —— 它早了、序错了、≈0 收益、且此刻不可验。**本文档定格为蓝图,实施门槛 = 识别层冻结 + #227。**

---

## §6 实施前必满足(检查清单)
- [ ] 识别层耐久验收通过(读那 1 小时长局:垃圾累积/守恒稳/分辨率不漂)；
- [ ] 识别层冻结(#235-B / #239 等识别改动 要么做完要么明确放弃)；
- [ ] 契约 #227 落地(定义 TickObservation / HandState 接口的不变量)；
- [ ] Win 可用(每刀行为保持需 live 回归)；
- [ ] 然后按 §4.3 顺序逐刀,每刀:Linux 编译 + 纯逻辑单测 → Win live 回归。
