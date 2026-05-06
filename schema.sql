-- ============================================================
-- Poker Assistant — initial schema (Phase 1)
-- Target: PostgreSQL 15+
-- ============================================================

-- ── Hands ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hands (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hand_number     INTEGER,
    table_name      TEXT NOT NULL,
    game_type       TEXT NOT NULL DEFAULT 'NLH',
    stakes          TEXT NOT NULL DEFAULT '0.00/0.00',

    hero_name       TEXT,
    hero_position   TEXT,
    hero_cards      JSONB,                  -- ["Ah", "Kd"]

    community_cards JSONB,                  -- {"preflop":[],"flop":["Ah","Kh","Qh"],...}
    seats           JSONB,                  -- {"BTN":"player1","SB":"hero",...}

    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,

    result          JSONB,                  -- {"win_loss":15.5,"showdown":true,...}
    raw_data        JSONB,                  -- extensibility

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Action Events ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS action_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hand_id             UUID NOT NULL REFERENCES hands(id) ON DELETE CASCADE,

    player_name         TEXT NOT NULL,
    position            TEXT NOT NULL,      -- SB/BB/UTG/MP/CO/BTN
    street              TEXT NOT NULL,      -- preflop/flop/turn/river/showdown
    action_type         TEXT NOT NULL,      -- fold/check/call/bet/raise/all_in/post_sb/post_bb/post_ante
    sequence_number     INTEGER NOT NULL,

    amount              DOUBLE PRECISION,   -- chips put in on this action
    facing_action       TEXT,               -- what action the player is responding to
    effective_stack_bb  DOUBLE PRECISION,
    pot_size_bb         DOUBLE PRECISION,
    players_in_pot      INTEGER DEFAULT 0,

    board_texture       JSONB,              -- {"wet":true,"paired":false,"high_card":"A","straight_draw":true,...}
    timestamp           TIMESTAMPTZ,

    raw_data            JSONB,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_action_events_hand ON action_events(hand_id);
CREATE INDEX IF NOT EXISTS idx_action_events_player ON action_events(player_name);
CREATE INDEX IF NOT EXISTS idx_action_events_hand_seq ON action_events(hand_id, sequence_number);

-- ── Player Stats Cache (rolled-up aggregate stats) ────────
CREATE TABLE IF NOT EXISTS player_stats_cache (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_name     TEXT NOT NULL UNIQUE,
    total_hands     INTEGER NOT NULL DEFAULT 0,

    -- Preflop
    vpip            DOUBLE PRECISION,
    pfr             DOUBLE PRECISION,
    af              DOUBLE PRECISION,
    three_bet_pct   DOUBLE PRECISION,
    fold_to_three_bet_pct DOUBLE PRECISION,
    ats             DOUBLE PRECISION,       -- attempt to steal
    call_open_pct   DOUBLE PRECISION,

    -- Postflop
    cbet_pct        DOUBLE PRECISION,
    fold_to_cbet_pct DOUBLE PRECISION,
    raise_cbet_pct  DOUBLE PRECISION,
    wtsd_pct        DOUBLE PRECISION,       -- went to showdown
    wsd_pct         DOUBLE PRECISION,       -- won at showdown
    double_barrel_pct DOUBLE PRECISION,
    check_raise_pct DOUBLE PRECISION,
    donk_bet_pct    DOUBLE PRECISION,

    last_updated    TIMESTAMPTZ NOT NULL DEFAULT now(),
    stats_json      JSONB                   -- extensibility bucket
);

-- ── Situational Stats (multi-dimensional) ─────────────────
CREATE TABLE IF NOT EXISTS player_situational_stats (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_name     TEXT NOT NULL,
    stat_type       TEXT NOT NULL,           -- e.g. 'vpip', 'pfr', 'cbet_pct', 'fold_to_cbet_pct'

    -- Dimension keys as a JSON object:
    -- {"stack_depth":"short","players_in_pot":"multiway","position_vs":"IP","board_texture":"wet","bet_size_bucket":"medium"}
    dimensions      JSONB NOT NULL,

    -- {"value":0.35,"sample_size":12}
    stat_value      JSONB NOT NULL,

    sample_size     INTEGER NOT NULL DEFAULT 0,
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sit_stats_player ON player_situational_stats(player_name);
CREATE INDEX IF NOT EXISTS idx_sit_stats_type ON player_situational_stats(player_name, stat_type);

-- ── Replay Corrections ────────────────────────────────────
CREATE TABLE IF NOT EXISTS replay_corrections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hand_id         UUID NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
    event_id        UUID REFERENCES action_events(id) ON DELETE SET NULL,

    correction_type TEXT NOT NULL,           -- action_type / amount / cards / player_cards / position
    original_value  JSONB,
    corrected_value JSONB NOT NULL,

    notes           TEXT,
    corrected_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_corrections_hand ON replay_corrections(hand_id);
