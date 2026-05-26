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
GROUP BY ae.player_name
HAVING COUNT(*) FILTER (WHERE ae.raw_data->>'decision_time_ms' IS NOT NULL) >= 3
ORDER BY n_timed DESC;


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
