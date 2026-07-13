-- Logical SQLite reference. Adapt to the repository's migration framework and
-- existing table names. Do not create duplicates when equivalent tables exist.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS strategy_current_config_versions (
    strategy_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (strategy_id, config_hash)
);

CREATE TABLE IF NOT EXISTS strategy_current_forecast_points (
    point_id TEXT PRIMARY KEY,
    target_date_local TEXT NOT NULL,
    model_key TEXT NOT NULL,
    source_variant TEXT NOT NULL,
    run_id TEXT NOT NULL,
    run_time_utc TEXT NOT NULL,
    source_available_at_utc TEXT NOT NULL,
    valid_time_utc TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    temperature_f REAL NOT NULL,
    raw_payload_id TEXT,
    UNIQUE(model_key, source_variant, run_id, valid_time_utc)
);
CREATE INDEX IF NOT EXISTS idx_strategy_current_forecast_asof
ON strategy_current_forecast_points(model_key, target_date_local, source_available_at_utc, received_at_utc, valid_time_utc);

CREATE TABLE IF NOT EXISTS strategy_current_observations (
    observation_id TEXT PRIMARY KEY,
    station TEXT NOT NULL,
    target_date_local TEXT NOT NULL,
    observation_time_utc TEXT NOT NULL,
    source_available_at_utc TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    temperature_f REAL NOT NULL,
    accepted INTEGER NOT NULL CHECK (accepted IN (0,1)),
    rejection_reason TEXT,
    raw_payload_id TEXT
);

CREATE TABLE IF NOT EXISTS strategy_current_orderbook_events (
    book_event_id TEXT PRIMARY KEY,
    market_ticker TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('snapshot','delta','invalidated')),
    subscription_id TEXT,
    sequence_number INTEGER,
    exchange_time_utc TEXT,
    received_at_utc TEXT NOT NULL,
    side TEXT,
    price_dollars TEXT,
    delta_count_fp TEXT,
    levels_json TEXT,
    valid_after_event INTEGER NOT NULL CHECK (valid_after_event IN (0,1)),
    raw_payload_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_strategy_current_book_seq
ON strategy_current_orderbook_events(market_ticker, received_at_utc, sequence_number);

CREATE TABLE IF NOT EXISTS strategy_current_public_trades (
    trade_id TEXT PRIMARY KEY,
    market_ticker TEXT NOT NULL,
    count_fp TEXT NOT NULL,
    yes_price_dollars TEXT NOT NULL,
    no_price_dollars TEXT NOT NULL,
    created_time_utc TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    is_block_trade INTEGER,
    page_number INTEGER,
    request_cursor TEXT,
    response_cursor TEXT,
    raw_payload_id TEXT
);

CREATE TABLE IF NOT EXISTS strategy_current_capture_manifests (
    capture_id TEXT PRIMARY KEY,
    target_date_local TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    observed_json TEXT NOT NULL,
    cursor_exhausted INTEGER NOT NULL,
    book_sequence_valid INTEGER NOT NULL,
    schema_valid INTEGER NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_current_decisions (
    decision_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    target_date_local TEXT NOT NULL,
    event_ticker TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    input_cutoff_utc TEXT NOT NULL,
    code_revision TEXT NOT NULL,
    capture_id TEXT,
    source_ids_json TEXT NOT NULL,
    forecast_summary_json TEXT NOT NULL,
    model_weights_json TEXT NOT NULL,
    probabilities_json TEXT NOT NULL,
    candidate_json TEXT,
    reason_code TEXT NOT NULL,
    mode TEXT NOT NULL,
    UNIQUE(strategy_id, target_date_local, evaluated_at_utc, config_hash)
);

CREATE TABLE IF NOT EXISTS strategy_current_model_states (
    decision_id TEXT NOT NULL,
    model_key TEXT NOT NULL,
    source_variant TEXT NOT NULL,
    raw_live_state_f REAL NOT NULL,
    corrected_point_f REAL,
    observed_max_f REAL,
    future_max_f REAL NOT NULL,
    residual_history_count INTEGER NOT NULL,
    effective_sample_size REAL,
    reliability_weight REAL,
    provenance_json TEXT NOT NULL,
    PRIMARY KEY(decision_id, model_key),
    FOREIGN KEY(decision_id) REFERENCES strategy_current_decisions(decision_id)
);

CREATE TABLE IF NOT EXISTS strategy_current_shadow_actions (
    shadow_action_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    market_ticker TEXT,
    side TEXT,
    action TEXT NOT NULL,
    quantity_fp TEXT,
    limit_price_dollars TEXT,
    conservative_probability REAL,
    expected_roi REAL,
    expected_value_dollars TEXT,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(decision_id) REFERENCES strategy_current_decisions(decision_id)
);
