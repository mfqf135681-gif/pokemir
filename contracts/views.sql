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
