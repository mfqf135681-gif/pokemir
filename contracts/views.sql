-- SQL views for cross-validation analysis & player profiling.
-- Apply with: psql ... -f contracts/views.sql
-- (Idempotent: uses CREATE OR REPLACE)

-- ── #2 Hand conservation check ──────────────────────────────────
-- Reverse-computes rake (= sum_initial − sum_final − net_pot_change) and flags
-- hands whose chip movement looks anomalous (off by > 10% + 30 chip cap).
-- Note: rebuy/topup between hands distorts this view; large positive deltas
--       on a single seat without all-in event = likely rebuy, not rake.
CREATE OR REPLACE VIEW v_hand_conservation AS
SELECT
  h.id AS hand_id,
  to_char(h.started_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI:SS') AS started_cn,
  h.pot_size_final AS pot,
  COALESCE(
    (SELECT SUM(v::float) FROM jsonb_each_text(h.raw_data->'player_stacks_initial') AS x(k, v)),
    0
  ) AS sum_init,
  COALESCE(
    (SELECT SUM(v::float) FROM jsonb_each_text(h.raw_data->'player_stacks_final') AS x(k, v)),
    0
  ) AS sum_final,
  -- Rake = chips that moved out of all stacks combined (assuming no rebuy mid-hand)
  COALESCE(
    (SELECT SUM(v::float) FROM jsonb_each_text(h.raw_data->'player_stacks_initial') AS x(k, v)),
    0
  ) - COALESCE(
    (SELECT SUM(v::float) FROM jsonb_each_text(h.raw_data->'player_stacks_final') AS x(k, v)),
    0
  ) AS chip_movement,
  -- Expected rake range: 0 to (pot * 0.10 + 30 cap)
  CASE
    WHEN h.pot_size_final IS NULL OR h.pot_size_final = 0 THEN 'NULL_POT'
    WHEN (
      COALESCE(
        (SELECT SUM(v::float) FROM jsonb_each_text(h.raw_data->'player_stacks_initial') AS x(k, v)),
        0
      ) - COALESCE(
        (SELECT SUM(v::float) FROM jsonb_each_text(h.raw_data->'player_stacks_final') AS x(k, v)),
        0
      )
    ) BETWEEN -10 AND (h.pot_size_final * 0.10 + 30) THEN 'OK'
    ELSE 'CHECK_REQUIRED'
  END AS status
FROM hands h
WHERE h.raw_data ? 'player_stacks_initial' AND h.raw_data ? 'player_stacks_final';


-- ── #12b Insurance buy rate per player ──────────────────────────
-- Aggregates insurance_inferred entries from hands.raw_data; computes per-player
-- "buy rate when all-in" — a signal for 水上/水下 (skilled vs recreational).
-- Higher buy rate = more risk-averse = often recreational player.
CREATE OR REPLACE VIEW v_player_insurance_stats AS
WITH ins AS (
  SELECT
    h.id,
    (jsonb_array_elements(h.raw_data->'insurance_inferred')) AS evt
  FROM hands h
  WHERE h.raw_data ? 'insurance_inferred'
)
SELECT
  (evt->>'player_name') AS player_name,
  COUNT(*) AS all_in_hands,
  SUM(CASE WHEN evt->>'classification' = 'insurance_payout' THEN 1 ELSE 0 END) AS bought_insurance,
  SUM(CASE WHEN evt->>'classification' = 'lost_no_insurance' THEN 1 ELSE 0 END) AS lost_no_ins,
  SUM(CASE WHEN evt->>'classification' = 'rebuy' THEN 1 ELSE 0 END) AS rebuy,
  ROUND(
    100.0 * SUM(CASE WHEN evt->>'classification' = 'insurance_payout' THEN 1 ELSE 0 END)
    / NULLIF(COUNT(*), 0),
    1
  ) AS insurance_buy_rate_pct
FROM ins
-- T64b(2026-05-29):排除 TempUser_ 空座 placeholder.
WHERE (evt->>'player_name') NOT LIKE 'TempUser_%'
GROUP BY player_name
ORDER BY all_in_hands DESC;


-- ── Low-confidence events (P4 review CLI target) ────────────────
-- Excludes auto-corrected events (override_reason IS NOT NULL) — those are
-- "auto-resolved by stack-derived inference" not "truly low signal".
-- After 2026-05-25 fix: override events now get confidence=0.7;
--   confidence < 0.7 means "no numerical signal available";
--   confidence == 0.7 may include both override AND multi-actor partial signals.
-- The override exclusion below is belt-and-suspenders.
CREATE OR REPLACE VIEW v_low_confidence_events AS
SELECT
  ae.id AS event_id, ae.hand_id, ae.player_name, ae.position, ae.street,
  ae.action_type, ae.amount, ae.confidence_score,
  ae.raw_data->>'stack_delta' AS stack_d,
  ae.raw_data->>'pot_delta' AS pot_d,
  ae.raw_data->>'action_text' AS text,
  ae.raw_data->>'text_derived_action' AS text_drv,
  ae.raw_data->>'stack_derived_action' AS stack_drv,
  ae.raw_data->>'override_reason' AS reason,
  ae.timestamp
FROM action_events ae
WHERE ae.confidence_score < 0.7
  AND ae.raw_data->>'override_reason' IS NULL
ORDER BY ae.timestamp DESC;


-- ── T3 Cross-hand stack continuity ──────────────────────────────
-- For each (hand N, seat X) compares the final stack at end-of-hand N to the
-- initial stack at start-of-hand N+1. Significant unexplained jumps (delta >
-- 50 chips AND not a round rebuy) signal OCR drift or untracked rebuy events.
CREATE OR REPLACE VIEW v_cross_hand_stack_continuity AS
WITH paired AS (
  SELECT
    h.id AS hand_id,
    h.started_at,
    h.raw_data->'player_stacks_final' AS final_s,
    LEAD(h.raw_data->'player_stacks_initial')
      OVER (ORDER BY h.started_at) AS next_init_s,
    LEAD(h.id) OVER (ORDER BY h.started_at) AS next_hand_id
  FROM hands h
  WHERE h.raw_data ? 'player_stacks_final'
)
SELECT
  p.hand_id,
  p.next_hand_id,
  to_char(p.started_at AT TIME ZONE 'Asia/Shanghai','MM-DD HH24:MI:SS') AS hand_ts,
  fp.key AS seat,
  fp.value::float AS stack_final_n,
  (p.next_init_s->>fp.key)::float AS stack_initial_n_plus_1,
  ((p.next_init_s->>fp.key)::float - fp.value::float) AS delta,
  CASE
    WHEN p.next_init_s->>fp.key IS NULL THEN 'absent_in_next'
    WHEN abs((p.next_init_s->>fp.key)::float - fp.value::float) <= 50 THEN 'OK'
    WHEN ((p.next_init_s->>fp.key)::float - fp.value::float) > 50
         AND (((p.next_init_s->>fp.key)::float - fp.value::float)::int % 50 = 0
              OR (p.next_init_s->>fp.key)::float IN (100,200,500,1000,2000,5000,10000))
      THEN 'rebuy'
    ELSE 'CHECK_REQUIRED'
  END AS status
FROM paired p, jsonb_each_text(p.final_s) AS fp
WHERE p.next_init_s IS NOT NULL
ORDER BY p.started_at DESC, fp.key;


-- ── Decision-time stats per player (for 水上/水下 profiling) ────
-- avg / median / std of decision_time_ms across action_events;
-- snap rate (< 3s) and timebank usage rate.
CREATE OR REPLACE VIEW v_player_timing_stats AS
SELECT
  ae.player_name,
  COUNT(*) FILTER (WHERE ae.raw_data->>'decision_time_ms' IS NOT NULL) AS n_timed,
  ROUND(AVG((ae.raw_data->>'decision_time_ms')::numeric)::numeric, 0) AS avg_ms,
  ROUND(PERCENTILE_CONT(0.5)
        WITHIN GROUP (ORDER BY (ae.raw_data->>'decision_time_ms')::numeric)
        ::numeric, 0) AS median_ms,
  ROUND(STDDEV((ae.raw_data->>'decision_time_ms')::numeric)::numeric, 0) AS std_ms,
  SUM(CASE WHEN (ae.raw_data->>'decision_time_ms')::numeric < 3000
           THEN 1 ELSE 0 END) AS n_snap,
  SUM(CASE WHEN (ae.raw_data->>'used_timebank')::boolean = true
           THEN 1 ELSE 0 END) AS n_timebank,
  ROUND(
    100.0 * SUM(CASE WHEN (ae.raw_data->>'decision_time_ms')::numeric < 3000
                     THEN 1 ELSE 0 END)
    / NULLIF(COUNT(*) FILTER (WHERE ae.raw_data->>'decision_time_ms' IS NOT NULL), 0),
    1
  ) AS snap_pct,
  ROUND(
    100.0 * SUM(CASE WHEN (ae.raw_data->>'used_timebank')::boolean = true
                     THEN 1 ELSE 0 END)
    / NULLIF(COUNT(*) FILTER (WHERE ae.raw_data->>'decision_time_ms' IS NOT NULL), 0),
    1
  ) AS timebank_pct
FROM action_events ae
-- T64b(2026-05-29):排除 TempUser_ 空座 placeholder.
WHERE ae.player_name NOT LIKE 'TempUser_%'
GROUP BY ae.player_name
HAVING COUNT(*) FILTER (WHERE ae.raw_data->>'decision_time_ms' IS NOT NULL) >= 3
ORDER BY n_timed DESC;


-- ── T3 Player action corrected (P3 fold/check bug fix view) ────
-- 2026-05-27: 修复历史 92 个 text="弃牌" 被 stack-derived 误覆盖为 check 的事件。
-- 原因:stack_delta=0 时 FOLD 和 CHECK 的 stack 签名相同,P3 当时无条件让
--      stack-derived 覆盖 text-derived,导致用户明确弃牌却落库为 check。
-- 修复(orchestrator.py 2026-05-27 T3): 新事件已修复;此 view 兜底历史数据。
-- 用法: SELECT * FROM v_player_action_corrected WHERE was_corrected = true;
--      dashboard / stats 查询应优先用此 view 替代 action_events 直查 action_type。
CREATE OR REPLACE VIEW v_player_action_corrected AS
SELECT
  ae.id AS event_id,
  ae.hand_id,
  ae.player_name,
  ae.position,
  ae.street,
  ae.sequence_number,
  -- 修正后的 action_type:text-derived FOLD/CHECK 强信号优先
  CASE
    WHEN ae.raw_data->>'text_derived_action' = 'fold'
         AND ae.action_type = 'check' THEN 'fold'
    WHEN ae.raw_data->>'text_derived_action' = 'check'
         AND ae.action_type = 'fold' THEN 'check'
    ELSE ae.action_type
  END AS action_type_corrected,
  ae.action_type AS action_type_raw,
  -- 标记是否经过修正(dashboard 可标 "T3 corrected")
  (
    (ae.raw_data->>'text_derived_action' = 'fold' AND ae.action_type = 'check')
    OR
    (ae.raw_data->>'text_derived_action' = 'check' AND ae.action_type = 'fold')
  ) AS was_corrected,
  ae.amount,
  ae.facing_action,
  ae.effective_stack_bb,
  ae.pot_size_bb,
  ae.confidence_score,
  ae.raw_data,
  ae.timestamp,
  ae.created_at
FROM action_events ae;


-- ── T4 Showdown physical integrity audit ───────────────────────
-- 2026-05-27: 标识 hands.raw_data->showdown_cards 中的物理违反:
--   1. cards[0] == cards[1]      (Gate 6a 漏网,同张手牌)
--   2. hole 牌出现在 community   (Gate 6c.A,跨手牌-公共牌冲突)
--   3. hole 牌跨座位重复          (Gate 6c.B,跨座位同张牌)
-- 旧数据约 12-15% accepted 摊牌违反此约束;新事件已由 orchestrator.py Gate 6c 拦截.
-- 用法: SELECT * FROM v_showdown_physical_check WHERE violation_count > 0;
--      dashboard 摊牌列表应过滤 violation_count = 0 的"干净"数据.
CREATE OR REPLACE VIEW v_showdown_physical_check AS
WITH showdown_seats AS (
  SELECT
    h.id AS hand_id,
    h.raw_data->'community_cards_final' AS community_final,
    seat.key AS seat_idx,
    ARRAY(SELECT jsonb_array_elements_text(seat.value)) AS cards
  FROM hands h, jsonb_each(h.raw_data->'showdown_cards') AS seat
  WHERE h.raw_data ? 'showdown_cards'
),
all_seats_per_hand AS (
  SELECT hand_id, ARRAY_AGG(seat_idx::int ORDER BY seat_idx::int) AS seat_indices,
         ARRAY_AGG(cards) AS cards_per_seat
  FROM showdown_seats
  GROUP BY hand_id
)
SELECT
  ss.hand_id,
  ss.seat_idx,
  ss.cards,
  -- Gate 6a: cards[0] == cards[1] (rebuilt from raw, defensive)
  (ss.cards[1] = ss.cards[2]) AS gate6a_same_pair,
  -- Gate 6c.A: hole in community (if community_final exists in raw_data)
  ss.community_final,
  -- Gate 6c.B: hole duplicates with OTHER seats in same hand
  (
    SELECT COUNT(*) FROM showdown_seats other
    WHERE other.hand_id = ss.hand_id
      AND other.seat_idx != ss.seat_idx
      AND (other.cards && ss.cards)  -- array overlap
  ) AS cross_seat_dup_count,
  -- Aggregate violation count
  (
    CASE WHEN ss.cards[1] = ss.cards[2] THEN 1 ELSE 0 END
    +
    (SELECT COUNT(*) FROM showdown_seats other
      WHERE other.hand_id = ss.hand_id
        AND other.seat_idx != ss.seat_idx
        AND (other.cards && ss.cards))
  ) AS violation_count
FROM showdown_seats ss
ORDER BY ss.hand_id, ss.seat_idx::int;


-- ── T27 Player net winnings(2026-05-28,排除 rebuy 突跳)──────
-- 按玩家算每手净胜负,识别 rebuy 突跳并排除,得到真实输赢趋势。
-- 用于 dashboard / 画像分析里"谁赢谁亏",修正之前 stack 突跳被误认为输赢的 bug。
--
-- 算法:
--   1. 每玩家每手取最后一个 stack_after(action_events DISTINCT ON)
--   2. 时序排列 + LAG 拿上一手 stack
--   3. delta = 本手 stack - 上手 stack
--   4. 分类:
--      - delta > 50 + 整数模板(100/200/300/.../10000) → REBUY
--      - |delta| <= 10                                 → OK(轻微 OCR 噪声)
--      - 其他                                          → NORMAL(真输赢)
--   5. 聚合:net_excl_rebuy = SUM(NORMAL/OK 的 delta)
--
-- ⚠️ 这是近似算法,**不精确,trend only**:
--   - 整桌 SUM(net) ≠ 0(应零和;实测偏 +5-10%,因为下面 3 个未补)
--   - 不含 rake(每手平台抽 5-10% / cap 30 chips,跨整桌才能算 → #LR4)
--   - 跨 session OCR 漂移可能把同一玩家拆成两个(豺狼I vs 豺狼I1)→ #T29
--   - 玩家中途下桌再坐回时 LAG 跨断层连续算
-- ✅ 仍有用:
--   - **相对排序**(谁赢谁亏 + 大小关系)方向正确
--   - dashboard 显示 trend / 找最大赢家亏家 OK
--   - 不能用作精确净胜负 / 商业财务级数字
CREATE OR REPLACE VIEW v_player_net_winnings AS
WITH player_last_event AS (
  SELECT DISTINCT ON (player_name, hand_id)
    player_name,
    hand_id,
    timestamp,
    (raw_data->>'stack_after')::float AS stack_after
  FROM action_events
  WHERE raw_data->>'stack_after' IS NOT NULL
  ORDER BY player_name, hand_id, sequence_number DESC
),
sequenced AS (
  SELECT
    player_name, hand_id, timestamp, stack_after,
    LAG(stack_after) OVER (PARTITION BY player_name ORDER BY timestamp) AS prev_hand_stack
  FROM player_last_event
),
deltas AS (
  SELECT
    player_name,
    hand_id,
    stack_after,
    prev_hand_stack,
    stack_after - prev_hand_stack AS delta_chips,
    CASE
      -- REBUY 检测:stack 突跳 > 50 且(50 倍数 OR 标准 buy-in 模板)
      -- (跟 v_cross_hand_stack_continuity 一致)
      WHEN (stack_after - prev_hand_stack) > 50
           AND (
             (stack_after - prev_hand_stack)::int % 50 = 0
             OR (stack_after - prev_hand_stack)::int IN
                (100, 200, 300, 500, 800, 1000, 1500, 2000, 3000, 5000, 10000)
           )
        THEN 'REBUY'
      WHEN abs(stack_after - prev_hand_stack) <= 10 THEN 'OK'
      ELSE 'NORMAL'
    END AS classification
  FROM sequenced
  WHERE prev_hand_stack IS NOT NULL
)
SELECT
  player_name AS 玩家,
  COUNT(*) AS hands_traced,
  COUNT(*) FILTER (WHERE classification = 'REBUY') AS rebuy_count,
  ROUND(COALESCE(SUM(delta_chips) FILTER (WHERE classification = 'REBUY'), 0)::numeric, 0) AS rebuy_total,
  ROUND(SUM(delta_chips) FILTER (WHERE classification != 'REBUY')::numeric, 0) AS net_excl_rebuy,
  ROUND(SUM(delta_chips)::numeric, 0) AS net_naive,
  ROUND(MIN(stack_after)::numeric, 0) AS min_stack,
  ROUND(MAX(stack_after)::numeric, 0) AS max_stack
FROM deltas
GROUP BY player_name
ORDER BY net_excl_rebuy DESC NULLS LAST;


-- ── T19 Player × Position 维度画像矩阵(2026-05-28)──────────────
-- 每玩家在每个 position(SB/BB/UTG/UTG+1/MP/HJ/CO/BTN)的 VPIP / PFR。
-- dashboard 用 — 找位置纪律好的(职业 TAG)vs 位置无感的(鱼)。
-- 样本量小时数字噪声大,dashboard 应过滤 hands >= 3。
CREATE OR REPLACE VIEW v_player_position_matrix AS
WITH ph AS (
  SELECT DISTINCT player_name, hand_id, position FROM action_events
  WHERE position IS NOT NULL
),
vpip AS (
  SELECT DISTINCT player_name, hand_id, position FROM action_events
  WHERE street='preflop' AND action_type IN ('call','bet','raise','all_in')
    AND position IS NOT NULL
),
pfr AS (
  SELECT DISTINCT player_name, hand_id, position FROM action_events
  WHERE street='preflop' AND action_type IN ('bet','raise','all_in')
    AND position IS NOT NULL
)
SELECT
  ph.player_name,
  ph.position,
  COUNT(DISTINCT ph.hand_id) AS hands,
  ROUND(100.0 * COUNT(DISTINCT vpip.hand_id) / NULLIF(COUNT(DISTINCT ph.hand_id),0), 0)::int AS vpip_pct,
  ROUND(100.0 * COUNT(DISTINCT pfr.hand_id) / NULLIF(COUNT(DISTINCT ph.hand_id),0), 0)::int AS pfr_pct
FROM ph
LEFT JOIN vpip ON vpip.player_name=ph.player_name AND vpip.hand_id=ph.hand_id AND vpip.position=ph.position
LEFT JOIN pfr ON pfr.player_name=ph.player_name AND pfr.hand_id=ph.hand_id AND pfr.position=ph.position
GROUP BY ph.player_name, ph.position;


-- ── T4 Hand duration sanity ─────────────────────────────────────
-- Typical hand: 20-180 seconds (median 30-120 with showdown).
--   < 10s  → likely finalize misfire (community blink, not a real hand end)
--   > 5min → likely hand-start/end detection failure or stuck pipeline
CREATE OR REPLACE VIEW v_hand_duration_sanity AS
SELECT
  id AS hand_id,
  to_char(started_at AT TIME ZONE 'Asia/Shanghai','MM-DD HH24:MI:SS') AS started_cn,
  ended_at - started_at AS duration,
  EXTRACT(EPOCH FROM (ended_at - started_at))::int AS dur_sec,
  pot_size_final AS pot,
  CASE
    WHEN ended_at IS NULL THEN 'in_progress'
    WHEN EXTRACT(EPOCH FROM (ended_at - started_at)) < 10 THEN 'TOO_FAST'
    WHEN EXTRACT(EPOCH FROM (ended_at - started_at)) > 300 THEN 'TOO_SLOW'
    ELSE 'OK'
  END AS status
FROM hands
WHERE ended_at IS NOT NULL
ORDER BY started_at DESC;


-- ═══════════════════════════════════════════════════════════════════
-- 🏗️ T68 圈梁(Ring Beam)派生 view 层(2026-05-29 立)
-- 用户提议:用高桩真值反推漏抓 events,view-only 不动主表.
-- 设计见 [[ring-beam-inference-design]].
-- 严约束:联立深度≤1 / 2+ corroborator / 资金流±rake.
-- Phase 1+2 — 20 维度首批 6 维(D1 D5 D7 D14 D22 D6).
-- ═══════════════════════════════════════════════════════════════════

-- ── 圈梁 D1: Pot 守恒 within-hand(底座) ────────────────────────
-- 检测相邻 captured events 间的 pot 跳变 vs 本 event 的 stack_delta.
-- pot_delta_observed > chip_captured + 容忍 → silent contribution 探测.
-- 容忍 = max(2 BB, 1% pot)的 OCR 噪音(within-hand 无 rake).
CREATE OR REPLACE VIEW v_ring_beam_pot_gaps AS
WITH ordered_events AS (
  SELECT
    hand_id, sequence_number, player_name, action_type, street,
    (raw_data->>'pot_after')::float AS pot_after_captured,
    (raw_data->>'stack_delta')::float AS stack_delta_captured,
    LAG((raw_data->>'pot_after')::float) OVER (
      PARTITION BY hand_id ORDER BY sequence_number
    ) AS prev_pot_after,
    LAG(sequence_number) OVER (
      PARTITION BY hand_id ORDER BY sequence_number
    ) AS prev_seq
  FROM action_events
)
SELECT
  hand_id,
  prev_seq AS gap_after_seq,
  sequence_number AS gap_before_seq,
  street,
  player_name AS observed_actor,
  action_type,
  pot_after_captured,
  prev_pot_after,
  (pot_after_captured - prev_pot_after) AS pot_delta_observed,
  COALESCE(stack_delta_captured, 0) AS chip_captured,
  (pot_after_captured - prev_pot_after - COALESCE(stack_delta_captured, 0)) AS silent_chip_amount,
  GREATEST(2.0, 0.01 * pot_after_captured) AS tolerance_within_hand,
  CASE
    WHEN (pot_after_captured - prev_pot_after - COALESCE(stack_delta_captured, 0))
         > GREATEST(2.0, 0.01 * pot_after_captured)
    THEN 'silent_action_detected'
    WHEN ABS(pot_after_captured - prev_pot_after - COALESCE(stack_delta_captured, 0))
         <= GREATEST(2.0, 0.01 * pot_after_captured)
    THEN 'ok'
    ELSE 'negative_drift'   -- pot 缩水,OCR 错可能性
  END AS status
FROM ordered_events
WHERE prev_pot_after IS NOT NULL
  AND pot_after_captured IS NOT NULL;


-- ── 圈梁 D5: Active set 按 street 维度 ──────────────────────────
-- 每 hand 内,每 street 玩家是否仍 active(未 fold).
-- "已 fold" = 任意 ≤ 本街 street 有 fold event.
-- 街内**实际行动玩家**:hand-end 前任一街 fold 过 = 此后所有街 inactive.
CREATE OR REPLACE VIEW v_ring_beam_active_per_street AS
WITH all_seat_players AS (
  SELECT DISTINCT h.id AS hand_id, s.value AS player_name, s.key AS position
  FROM hands h, jsonb_each_text(h.seats) AS s
  WHERE h.seats IS NOT NULL
),
fold_events AS (
  SELECT hand_id, player_name,
         MIN(sequence_number) AS fold_seq,
         MIN(street) AS fold_street
  FROM action_events
  WHERE action_type = 'fold'
  GROUP BY hand_id, player_name
),
streets AS (
  SELECT unnest(ARRAY['preflop','flop','turn','river']::text[]) AS street_name,
         unnest(ARRAY[1,2,3,4]::int[]) AS street_order
)
SELECT
  asp.hand_id,
  asp.player_name,
  asp.position,
  s.street_name AS street,
  s.street_order,
  fe.fold_seq,
  fe.fold_street,
  CASE
    WHEN fe.fold_seq IS NULL THEN TRUE  -- 全程没 fold = 一直 active(或漏抓 fold)
    WHEN s.street_order < (
      CASE fe.fold_street
        WHEN 'preflop' THEN 1
        WHEN 'flop' THEN 2
        WHEN 'turn' THEN 3
        WHEN 'river' THEN 4
        ELSE 5
      END
    ) THEN TRUE   -- 本街早于 fold 街,仍 active
    WHEN s.street_order = (
      CASE fe.fold_street
        WHEN 'preflop' THEN 1
        WHEN 'flop' THEN 2
        WHEN 'turn' THEN 3
        WHEN 'river' THEN 4
        ELSE 5
      END
    ) THEN TRUE   -- fold 当街:fold 那一刻前曾 active
    ELSE FALSE    -- 已 fold 街之后:必 inactive
  END AS is_active
FROM all_seat_players asp
CROSS JOIN streets s
LEFT JOIN fold_events fe USING (hand_id, player_name);


-- ── 圈梁 D7: Post-flop 位置确定顺序 ─────────────────────────────
-- Post-flop 行动顺序:SB → BB → UTG → UTG+1 → MP → MP+1 → HJ → CO → BTN
-- (无 SB 时从 BB 起;依此类推).
-- 给定 hand 的 button_seat_index + num_seats,可 deterministically 算出.
-- 圈梁用:推断 silent action 的 seq 应该插哪.
CREATE OR REPLACE VIEW v_ring_beam_postflop_order AS
WITH position_rank AS (
  SELECT 'SB' AS pos, 1 AS post_flop_order UNION ALL
  SELECT 'BB', 2 UNION ALL
  SELECT 'UTG', 3 UNION ALL
  SELECT 'UTG+1', 4 UNION ALL
  SELECT 'MP', 5 UNION ALL
  SELECT 'MP+1', 6 UNION ALL
  SELECT 'HJ', 7 UNION ALL
  SELECT 'CO', 8 UNION ALL
  SELECT 'BTN', 9
)
SELECT
  h.id AS hand_id,
  s.key AS position,
  s.value AS player_name,
  pr.post_flop_order
FROM hands h, jsonb_each_text(h.seats) AS s
LEFT JOIN position_rank pr ON pr.pos = s.key
WHERE h.seats IS NOT NULL
ORDER BY h.id, pr.post_flop_order NULLS LAST;


-- ── 圈梁 D14: Card uniqueness check(形式化) ──────────────────
-- 一手内所有牌(hero + community + showdown)必 unique(52 cards 约束).
-- 已 pipeline log 警告;此 view 形式化为可 SQL query 的检测.
CREATE OR REPLACE VIEW v_ring_beam_card_uniqueness AS
WITH all_cards AS (
  SELECT h.id AS hand_id, jsonb_array_elements_text(h.hero_cards) AS card, 'hero' AS source
  FROM hands h
  WHERE h.hero_cards IS NOT NULL AND jsonb_array_length(h.hero_cards) > 0
  UNION ALL
  SELECT h.id AS hand_id, jsonb_array_elements_text(h.community_cards->'turn') AS card, 'community' AS source
  FROM hands h
  WHERE h.community_cards->'turn' IS NOT NULL
  UNION ALL
  SELECT h.id, jsonb_array_elements_text(seat.value) AS card, 'showdown' AS source
  FROM hands h, jsonb_each(h.raw_data->'showdown_cards') seat
  WHERE h.raw_data ? 'showdown_cards'
),
card_counts AS (
  SELECT hand_id, card, COUNT(*) AS occurrences,
         ARRAY_AGG(source) AS sources
  FROM all_cards
  GROUP BY hand_id, card
)
SELECT
  hand_id,
  card,
  occurrences,
  sources,
  CASE WHEN occurrences > 1 THEN 'duplicate_violation' ELSE 'unique' END AS status
FROM card_counts;


-- ── 圈梁 D22: BB option(unraised preflop)── ───────────────────
-- 规则:Preflop 无 raise → BB 唯一合规选项 = check.
-- 触发:hand 内 preflop 无 raise event + BB 无 event(漏抓).
-- 推断:BB check.
CREATE OR REPLACE VIEW v_ring_beam_bb_option AS
WITH preflop_raises AS (
  SELECT hand_id, COUNT(*) AS n_raises
  FROM action_events
  WHERE street = 'preflop' AND action_type IN ('raise', 'bet', 'all_in')
  GROUP BY hand_id
),
bb_preflop_events AS (
  SELECT hand_id, player_name, COUNT(*) AS n_events
  FROM action_events
  WHERE street = 'preflop' AND position = 'BB'
    AND action_type NOT IN ('post_bb', 'post_sb')
  GROUP BY hand_id, player_name
),
seat_bb AS (
  SELECT h.id AS hand_id, h.seats->>'BB' AS bb_player
  FROM hands h
  WHERE h.seats ? 'BB'
)
SELECT
  sb.hand_id,
  sb.bb_player AS player_name,
  COALESCE(pr.n_raises, 0) AS preflop_raises,
  COALESCE(bb.n_events, 0) AS bb_preflop_events,
  CASE
    WHEN COALESCE(pr.n_raises, 0) = 0 AND COALESCE(bb.n_events, 0) = 0
    THEN 'bb_check_inferred'    -- 治根 silent BB check
    WHEN COALESCE(pr.n_raises, 0) = 0 AND COALESCE(bb.n_events, 0) > 0
    THEN 'bb_check_captured'
    ELSE 'not_applicable'
  END AS inference_status,
  0.95 AS joint_confidence    -- D22 单维已极高桩(规则铁)
FROM seat_bb sb
LEFT JOIN preflop_raises pr USING (hand_id)
LEFT JOIN bb_preflop_events bb USING (hand_id);


-- ── 圈梁 D6: Timer 指针推断 action(T48 数据复用) ─────────────
-- T48 v3 Stage 1 已 emit `pointer.action_inferred` diag(shadow 模式).
-- 圈梁正式启用:timer 从 A → B 转移 + A 无 event 入库 → A 必 silent acted.
-- amount 由 D1 + D5 联立推(此 view 只标记"哪些 timer 转移对应 silent action").
CREATE OR REPLACE VIEW v_ring_beam_timer_inferences AS
SELECT
  de.hand_id,
  (de.payload->>'seat')::int AS seat_index,
  (de.payload->>'last_timer_value')::int AS last_timer_value,
  de.payload->>'reason' AS reason,
  (de.payload->>'next_seat')::int AS next_seat,
  de.occurred_at,
  0.85 AS joint_confidence    -- D6 单维高桩(T48 设计已是 shadow 模式)
FROM diagnostic_events de
WHERE de.tag = 'pointer.action_inferred'
  AND de.payload->>'reason' = 'timer_moved_to_next';


-- ── 圈梁 D11: Min raise rule(sanity) ───────────────────────────
-- 规则:no-limit hold'em raise size ≥ 前 raise size.
-- 检测:连续两 raise 第二个不满足 min-raise → OCR 错或漏抓中间 raise.
CREATE OR REPLACE VIEW v_ring_beam_min_raise_check AS
WITH raise_events AS (
  SELECT
    hand_id, sequence_number, street, player_name,
    amount,
    LAG(amount) OVER (PARTITION BY hand_id, street ORDER BY sequence_number) AS prev_raise_amount,
    LAG(sequence_number) OVER (PARTITION BY hand_id, street ORDER BY sequence_number) AS prev_seq
  FROM action_events
  WHERE action_type IN ('raise', 'bet') AND amount IS NOT NULL
)
SELECT
  hand_id, sequence_number, prev_seq, street, player_name,
  amount AS this_raise,
  prev_raise_amount AS prev_raise,
  (amount - prev_raise_amount) AS raise_size_increment,
  CASE
    WHEN prev_raise_amount IS NULL THEN 'first_aggression'
    WHEN (amount - prev_raise_amount) >= prev_raise_amount THEN 'ok'
    ELSE 'min_raise_violation'   -- 漏抓中间 raise 或 OCR amount 错
  END AS status
FROM raise_events
WHERE prev_raise_amount IS NOT NULL;


-- ── 圈梁 D13: Effective stack cap(sanity) ─────────────────────
-- 规则:任何 bet/raise/call/all_in 不能超过该玩家 stack.
-- 检测:amount > stack_before → OCR 错.
CREATE OR REPLACE VIEW v_ring_beam_stack_cap_check AS
SELECT
  hand_id, sequence_number, player_name, action_type,
  amount,
  (raw_data->>'stack_before')::float AS stack_before,
  CASE
    WHEN amount IS NULL OR (raw_data->>'stack_before') IS NULL THEN 'no_data'
    WHEN amount <= (raw_data->>'stack_before')::float THEN 'ok'
    ELSE 'stack_cap_violation'
  END AS status
FROM action_events
WHERE action_type IN ('bet', 'raise', 'call', 'all_in')
  AND amount IS NOT NULL;


-- ── 圈梁 D9: All-in cap subsequent ────────────────────────────
-- 规则:all-in 玩家 commit 全 stack;后续 call ≤ all-in size(超出归 side pot).
-- 检测:hand 内 all-in 后某玩家 call 量 > all-in size + 容忍 → side pot 或 OCR 错.
CREATE OR REPLACE VIEW v_ring_beam_allin_followups AS
WITH allin_events AS (
  SELECT hand_id, sequence_number AS allin_seq, player_name AS allin_player,
         (raw_data->>'stack_delta')::float AS allin_amount
  FROM action_events
  WHERE action_type = 'all_in'
),
followup_calls AS (
  SELECT ae.hand_id, ae.sequence_number, ae.player_name AS call_player, ae.amount AS call_amount,
         a.allin_seq, a.allin_player, a.allin_amount
  FROM action_events ae
  JOIN allin_events a ON ae.hand_id = a.hand_id AND ae.sequence_number > a.allin_seq
  WHERE ae.action_type = 'call' AND ae.amount IS NOT NULL
)
SELECT
  hand_id, allin_seq, allin_player, allin_amount,
  sequence_number, call_player, call_amount,
  CASE
    WHEN call_amount <= allin_amount + 2 THEN 'within_cap'
    ELSE 'side_pot_or_error'
  END AS status
FROM followup_calls;


-- ── 圈梁 D2: Action 序列 2+ corroborator ───────────────────────
-- 严约束:2+ 个真 call 同 amount 同 hand 同 street + 中间无 bet → silent bet 推断.
CREATE OR REPLACE VIEW v_ring_beam_2plus_corroborator AS
WITH call_groups AS (
  SELECT
    hand_id, street, amount,
    COUNT(*) AS n_calls,
    ARRAY_AGG(player_name ORDER BY sequence_number) AS callers,
    ARRAY_AGG(sequence_number ORDER BY sequence_number) AS call_seqs
  FROM action_events
  WHERE action_type = 'call' AND amount IS NOT NULL
  GROUP BY hand_id, street, amount
  HAVING COUNT(*) >= 2
)
SELECT
  hand_id, street, amount AS inferred_bet_amount,
  n_calls AS corroborator_count, callers, call_seqs,
  0.95 AS joint_confidence,
  'silent_bet_inferred_2plus' AS status
FROM call_groups;


-- ── 圈梁 D21: UTG opens preflop ────────────────────────────────
-- 规则:UTG 是 preflop 第 1 个非 blind 行动者.
-- 检测:第 1 个非 POST 玩家 ≠ UTG → UTG 必 silent acted.
CREATE OR REPLACE VIEW v_ring_beam_utg_opens AS
WITH first_voluntary AS (
  SELECT DISTINCT ON (hand_id) hand_id, sequence_number, player_name, position
  FROM action_events
  WHERE street = 'preflop' AND action_type NOT IN ('post_sb', 'post_bb')
  ORDER BY hand_id, sequence_number
),
utg_seat AS (
  SELECT h.id AS hand_id, h.seats->>'UTG' AS utg_player
  FROM hands h
  WHERE h.seats ? 'UTG'
)
SELECT
  fv.hand_id, fv.sequence_number AS first_voluntary_seq,
  fv.player_name AS first_actor, fv.position AS first_position,
  us.utg_player,
  CASE
    WHEN fv.position = 'UTG' THEN 'utg_captured'
    WHEN fv.player_name = us.utg_player THEN 'utg_captured'
    ELSE 'utg_silent_inferred'   -- 第 1 actor 非 UTG = UTG 漏抓
  END AS status
FROM first_voluntary fv
LEFT JOIN utg_seat us USING (hand_id);


-- ── 圈梁 D3: Showdown 闭环 aggregate ───────────────────────────
-- 每 hand 是否有 showdown(`raw_data.showdown_cards` 存在).
CREATE OR REPLACE VIEW v_ring_beam_showdown_flag AS
SELECT
  id AS hand_id,
  (raw_data ? 'showdown_cards') AS has_showdown,
  jsonb_object_keys(COALESCE(raw_data->'showdown_cards', '{}'::jsonb)) AS showdown_seat
FROM hands;


-- ── 圈梁 D10: Showdown by seat(修 T69)────────────────────────
-- per-seat 摊牌 = 该 seat 必未 fold 全程,4 街都 active.
-- T69 fix:用 button_seat_index 计算 physical seat_index → position → player.
-- 旧版 cross-join 所有 position 到 showdown seat → 误归属.
CREATE OR REPLACE VIEW v_ring_beam_showdown_by_seat AS
WITH position_offsets AS (
  -- BTN 是 offset 0,从 BTN 顺时针(seat_index 递增)
  SELECT 0 AS pos_offset, 'BTN' AS position_name UNION ALL
  SELECT 1, 'SB' UNION ALL
  SELECT 2, 'BB' UNION ALL
  SELECT 3, 'UTG' UNION ALL
  SELECT 4, 'UTG+1' UNION ALL
  SELECT 5, 'MP' UNION ALL
  SELECT 6, 'MP+1' UNION ALL
  SELECT 7, 'HJ' UNION ALL
  SELECT 8, 'CO'
),
showdown_with_btn AS (
  SELECT
    h.id AS hand_id,
    (h.raw_data->>'button_seat_index')::int AS btn_idx,
    COALESCE(h.raw_data->>'num_seats', '9')::int AS num_seats,
    seat.key::int AS seat_idx,
    seat.value AS cards,
    h.seats AS seats_json
  FROM hands h, jsonb_each(h.raw_data->'showdown_cards') seat
  WHERE h.raw_data ? 'showdown_cards'
    AND h.raw_data ? 'button_seat_index'
)
SELECT
  s.hand_id,
  s.seat_idx,
  ((s.seat_idx - s.btn_idx + s.num_seats) % s.num_seats) AS pos_offset,
  po.position_name AS position,
  s.cards,
  (s.seats_json->>po.position_name) AS player_name,
  0.9 AS joint_confidence,
  'reach_showdown_all_streets_active' AS implication
FROM showdown_with_btn s
LEFT JOIN position_offsets po
  ON po.pos_offset = ((s.seat_idx - s.btn_idx + s.num_seats) % s.num_seats)
WHERE po.position_name IS NOT NULL
  AND (s.seats_json->>po.position_name) IS NOT NULL;


-- ── 圈梁 D15: Hand-end 无 showdown = 全 fold ───────────────────
-- 规则:hand 结束无 showdown → N-1 player 必 fold 某街.
-- 检测:per-hand 已捕获 fold count vs 玩家总数,缺口 = silent fold.
CREATE OR REPLACE VIEW v_ring_beam_handend_fold_inference AS
WITH seat_count AS (
  SELECT h.id AS hand_id,
         COUNT(*) AS n_seated,
         h.raw_data ? 'showdown_cards' AS has_showdown
  FROM hands h, jsonb_each_text(COALESCE(h.seats, '{}'::jsonb)) s
  GROUP BY h.id, h.raw_data ? 'showdown_cards'
),
fold_count AS (
  SELECT hand_id, COUNT(DISTINCT player_name) AS n_folds_captured
  FROM action_events WHERE action_type = 'fold'
  GROUP BY hand_id
)
SELECT
  sc.hand_id, sc.n_seated, sc.has_showdown,
  COALESCE(fc.n_folds_captured, 0) AS folds_captured,
  CASE
    WHEN sc.has_showdown THEN sc.n_seated - 1 - COALESCE(fc.n_folds_captured, 0)
    ELSE sc.n_seated - 1 - COALESCE(fc.n_folds_captured, 0)
  END AS silent_folds_inferred,
  0.85 AS joint_confidence
FROM seat_count sc
LEFT JOIN fold_count fc USING (hand_id);


-- ── 圈梁 D8: Heads-up alternation(2 人末段池) ─────────────────
-- 2 人池子内,严格 alternate.
-- 检测:相同 player 连续 2 个 events 中间无另玩家 event → silent action 漏.
CREATE OR REPLACE VIEW v_ring_beam_headsup_alternation AS
WITH evs AS (
  SELECT
    hand_id, sequence_number, street, player_name,
    LAG(player_name) OVER (PARTITION BY hand_id, street ORDER BY sequence_number) AS prev_player,
    LAG(sequence_number) OVER (PARTITION BY hand_id, street ORDER BY sequence_number) AS prev_seq
  FROM action_events
  WHERE action_type NOT IN ('post_sb', 'post_bb')
)
SELECT
  hand_id, sequence_number, prev_seq, street,
  player_name AS current_player, prev_player,
  CASE
    WHEN prev_player IS NULL THEN 'first'
    WHEN player_name = prev_player THEN 'silent_actor_between'   -- 同 player 连续 → 中间漏抓
    ELSE 'alternation_ok'
  END AS status
FROM evs;


-- ── 圈梁 D24: Last aggressor shows first(showdown 顺序) ──────
-- 规则:river 摊牌顺序 last aggressor 先出.
-- 推断:showdown 第 1 个出的 player = river last aggressor(若有 river bet).
-- 这里只把 showdown_cards 的第 1 个 seat 标记为 last aggressor 候选.
CREATE OR REPLACE VIEW v_ring_beam_last_aggressor AS
WITH showdown_order AS (
  SELECT
    h.id AS hand_id,
    seat.key AS first_showdown_seat,
    ROW_NUMBER() OVER (PARTITION BY h.id ORDER BY seat.key) AS rn
  FROM hands h, jsonb_each(h.raw_data->'showdown_cards') seat
  WHERE h.raw_data ? 'showdown_cards'
)
SELECT
  hand_id, first_showdown_seat AS last_aggressor_seat_candidate,
  0.85 AS joint_confidence
FROM showdown_order WHERE rn = 1;


-- ── 圈梁 D25: Rake invariant + 学习底座 ────────────────────────
-- pot vs win_amount 差额 = rake.
-- 合理 rake 范围:3-7% of pot,cap 30 BB.
-- per-hand 观察 + 跨手聚合 = per-table baseline 学习.
-- 用 player_stacks_initial/final 反推 rake(无需 win_amount field).
-- rake = sum(stacks_initial) - sum(stacks_final).
-- 正值 = rake;负值 = rebuy.
CREATE OR REPLACE VIEW v_ring_beam_rake_observed AS
WITH stacks AS (
  SELECT
    h.id AS hand_id, h.pot_size_final,
    COALESCE((SELECT SUM(v::float) FROM jsonb_each_text(h.raw_data->'player_stacks_initial') AS x(k, v)), 0) AS sum_init,
    COALESCE((SELECT SUM(v::float) FROM jsonb_each_text(h.raw_data->'player_stacks_final') AS x(k, v)), 0) AS sum_final
  FROM hands h
  WHERE h.raw_data ? 'player_stacks_initial' AND h.raw_data ? 'player_stacks_final'
)
SELECT
  hand_id, pot_size_final AS pot,
  sum_init, sum_final,
  (sum_init - sum_final) AS rake_observed,
  CASE
    WHEN pot_size_final IS NULL OR pot_size_final = 0 THEN NULL
    ELSE (sum_init - sum_final) / pot_size_final
  END AS rake_pct,
  CASE
    WHEN pot_size_final IS NULL THEN 'no_pot'
    WHEN (sum_init - sum_final) < -10 THEN 'rebuy_likely'
    WHEN pot_size_final > 0 AND (sum_init - sum_final) > 0.10 * pot_size_final + 30 THEN 'rake_high'
    WHEN ABS(sum_init - sum_final) <= 10 THEN 'no_flop_no_drop_or_tiny'
    ELSE 'normal_rake'
  END AS status
FROM stacks;


-- per-table rake baseline 学习(聚合所有 normal_rake)
CREATE OR REPLACE VIEW v_ring_beam_rake_baseline AS
SELECT
  h.table_name,
  COUNT(*) AS n_normal_hands,
  ROUND(AVG(rake_pct)::numeric, 4) AS avg_rake_pct,
  ROUND(STDDEV(rake_pct)::numeric, 4) AS std_rake_pct,
  MAX(rake_observed) AS max_rake_observed
FROM v_ring_beam_rake_observed rv
JOIN hands h ON h.id = rv.hand_id
WHERE rv.status = 'normal_rake'
GROUP BY h.table_name
HAVING COUNT(*) >= 20;   -- cold start ≥ 20 手才出 baseline


-- ── 圈梁 D26: 保险存在性 signature ─────────────────────────────
-- 规则:玩家 all-in 输 → stack_after_hand = 0
-- 反推:all-in 输 + stack_after > 0 → 必买保险.
-- 简化(无 D23 跨手数据):仅在 hand 内 all-in event 之后追踪 stack 痕迹.
CREATE OR REPLACE VIEW v_ring_beam_insurance_signature AS
WITH allin_events AS (
  SELECT
    hand_id, sequence_number, player_name,
    (raw_data->>'stack_after')::float AS stack_after_allin
  FROM action_events
  WHERE action_type = 'all_in'
),
hand_winners AS (
  SELECT
    id AS hand_id,
    raw_data->>'winner_player_name' AS winner   -- 假设字段名,需 verify
  FROM hands
)
SELECT
  ae.hand_id, ae.player_name, ae.sequence_number,
  ae.stack_after_allin,
  hw.winner,
  CASE
    WHEN hw.winner IS NULL THEN 'winner_unknown'
    WHEN hw.winner = ae.player_name THEN 'allin_won'
    WHEN ae.stack_after_allin = 0 THEN 'allin_lost_no_insurance'
    WHEN ae.stack_after_allin > 0 THEN 'allin_lost_with_insurance'  -- 治根
    ELSE 'unclear'
  END AS insurance_status,
  0.85 AS joint_confidence
FROM allin_events ae
LEFT JOIN hand_winners hw USING (hand_id);


-- ── 圈梁 D28: 边池数学分解(免抓 UI) ──────────────────────────
-- 规则:多 all-in → main pot + N side pots
-- main pot = 最小 all-in × caller 数;side k = Δ × caller_k 以上人数.
-- 简化版:列出 hand 内所有 all-in amounts 排序,用户后续可手算 / Phase B 加工.
CREATE OR REPLACE VIEW v_ring_beam_allin_amounts_per_hand AS
SELECT
  hand_id,
  COUNT(*) AS n_allins,
  ARRAY_AGG((raw_data->>'stack_delta')::float ORDER BY (raw_data->>'stack_delta')::float NULLS LAST) AS allin_amounts_sorted,
  ARRAY_AGG(player_name ORDER BY (raw_data->>'stack_delta')::float NULLS LAST) AS allin_players_sorted
FROM action_events
WHERE action_type = 'all_in'
GROUP BY hand_id;


-- ── 圈梁 D27: 保险金额(D23 依赖,标占位) ──────────────────────
-- 注:完整 D27 需 D23 跨手 stack 数据(Q3 未含本批).
-- 此版本仅基于 D26 signature 标记"有保险但未量化".
CREATE OR REPLACE VIEW v_ring_beam_insurance_amount_pending AS
SELECT
  hand_id, player_name,
  stack_after_allin,
  insurance_status,
  'amount_pending_d23' AS amount_inference_status,
  '需要 D23 跨手 stack 数据才能反推 premium + payout' AS note,
  0.6 AS confidence_until_d23_added
FROM v_ring_beam_insurance_signature
WHERE insurance_status = 'allin_lost_with_insurance';


-- ── 圈梁 D23: Stack 跨手严格连续(±rake)── T69 新增 ──────────
-- 规则:玩家 hand N 末 stack ≈ hand N+1 起 stack(无 rebuy).
-- 容忍 = rake(5% pot + cap 30)+ OCR 噪音(2 BB).
-- 负 delta(stack 增加)= rebuy 信号;大 delta = 不连续.
CREATE OR REPLACE VIEW v_ring_beam_stack_cross_hand AS
WITH consecutive AS (
  SELECT
    h.id AS hand_id,
    h.started_at,
    LAG(h.id) OVER (ORDER BY h.started_at) AS prev_hand_id,
    h.raw_data->'player_stacks_initial' AS this_init,
    LAG(h.raw_data->'player_stacks_final') OVER (ORDER BY h.started_at) AS prev_final,
    h.pot_size_final AS this_pot
  FROM hands h
)
SELECT
  c.hand_id, c.prev_hand_id,
  seat.key AS seat_idx,
  (c.prev_final->>seat.key)::float AS prev_final_stack,
  (c.this_init->>seat.key)::float AS this_init_stack,
  ((c.this_init->>seat.key)::float - (c.prev_final->>seat.key)::float) AS delta,
  GREATEST(2.0, 0.05 * c.this_pot + 30) AS tolerance_rake_aware,
  CASE
    WHEN c.prev_final->>seat.key IS NULL THEN 'no_prev_data'
    WHEN ABS((c.this_init->>seat.key)::float - (c.prev_final->>seat.key)::float) <= 2 THEN 'continuous'
    WHEN ((c.this_init->>seat.key)::float - (c.prev_final->>seat.key)::float) BETWEEN -GREATEST(30.0, 0.10 * c.this_pot) AND -2
      THEN 'rake_or_noise_loss'
    WHEN ((c.this_init->>seat.key)::float - (c.prev_final->>seat.key)::float) > 100 THEN 'rebuy_or_topup'
    WHEN ((c.this_init->>seat.key)::float - (c.prev_final->>seat.key)::float) > 2 THEN 'small_increase_unusual'
    ELSE 'discontinuity_check'
  END AS status,
  0.8 AS joint_confidence
FROM consecutive c, jsonb_each_text(c.this_init) seat
WHERE c.prev_final IS NOT NULL;


-- ── 圈梁 D26 v2: 保险存在性(D23-enhanced)── T69 加强 ─────────
-- 用 D23 跨手 stack:all-in 玩家 hand N 末 stack > 0 = 必买保险.
CREATE OR REPLACE VIEW v_ring_beam_insurance_v2 AS
WITH allin_hands AS (
  -- 找出有 all-in event 的 hand + 该玩家在哪个 physical seat
  SELECT DISTINCT
    ae.hand_id, ae.player_name,
    ae.sequence_number AS allin_seq
  FROM action_events ae
  WHERE ae.action_type = 'all_in'
),
seat_lookup AS (
  -- player_name → seat_index via seats JSON + button_seat_index 位置反推
  -- 简化版:用 player_stacks_final 检查哪个 seat 该玩家在
  -- (实际更准的方法是 join button position rotation,后续 enhance)
  SELECT
    h.id AS hand_id,
    s.key AS position,
    s.value AS player_name,
    h.raw_data AS hand_raw
  FROM hands h, jsonb_each_text(h.seats) s
  WHERE h.seats IS NOT NULL
),
allin_with_final_stack AS (
  SELECT
    a.hand_id, a.player_name, a.allin_seq,
    sl.position,
    -- 跨手 D23 信号:下一手起始 stack(若同玩家)
    -- 这里简化:用 player_stacks_final 反查
    sl.hand_raw->'player_stacks_final' AS final_stacks
  FROM allin_hands a
  LEFT JOIN seat_lookup sl ON sl.hand_id = a.hand_id AND sl.player_name = a.player_name
)
SELECT
  a.hand_id, a.player_name, a.allin_seq, a.position,
  -- 简化版:不依赖 button mapping,直接查 final_stacks 任意值 > 0 都标记需 D23 反查
  0.7 AS joint_confidence_pending_d23_seat_resolution,
  'requires_d23_seat_resolution' AS status,
  'D26 完整版需要 D23 + button_seat_index 反推 player physical seat,本 view 仅记 candidate' AS note
FROM allin_with_final_stack a;
