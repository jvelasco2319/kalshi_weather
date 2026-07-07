-- Optional schema additions for the fake-money journal.
-- Codex should adapt names/types to the existing repo schema.

CREATE TABLE IF NOT EXISTS profile_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    ts_utc TEXT NOT NULL,
    active_profile TEXT NOT NULL,
    previous_profile TEXT,
    profile_reason TEXT,
    effective_risk_config_json TEXT NOT NULL,
    profile_overrides_json TEXT,
    dynamic_overrides_json TEXT
);

CREATE TABLE IF NOT EXISTS settlement_scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    ts_utc TEXT NOT NULL,
    scenario_label TEXT NOT NULL,
    final_equity_dollars REAL NOT NULL,
    pnl_vs_starting_cash REAL NOT NULL,
    pnl_vs_current_equity REAL NOT NULL,
    settlement_value_dollars REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS position_thesis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    position_id TEXT NOT NULL,
    fill_id TEXT,
    ts_utc TEXT NOT NULL,
    bracket TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price_cents REAL NOT NULL,
    entry_probability_json TEXT NOT NULL,
    entry_model_state_json TEXT NOT NULL,
    entry_profile TEXT NOT NULL,
    take_profit_json TEXT,
    invalidation_rules_json TEXT
);

CREATE TABLE IF NOT EXISTS position_quality (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    ts_utc TEXT NOT NULL,
    position_id TEXT NOT NULL,
    quality_label TEXT NOT NULL,
    quality_score REAL,
    current_edge_cents REAL,
    edge_decay_cents REAL,
    probability_decay REAL,
    close_recommended INTEGER NOT NULL,
    close_reasons_json TEXT
);

CREATE TABLE IF NOT EXISTS close_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    ts_utc TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    position_id TEXT NOT NULL,
    bracket TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity_to_close INTEGER NOT NULL,
    estimated_exit_price_cents REAL,
    realized_pnl_if_closed REAL,
    close_reason_codes_json TEXT NOT NULL,
    close_priority_score REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS clv_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    fill_id TEXT NOT NULL,
    position_id TEXT,
    ts_utc TEXT NOT NULL,
    horizon TEXT NOT NULL,
    bracket TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price_cents REAL NOT NULL,
    mark_cents REAL,
    clv_cents REAL,
    adverse_selection_flag INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS thesis_exposure (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    ts_utc TEXT NOT NULL,
    thesis_label TEXT NOT NULL,
    thesis_direction TEXT,
    correlated_risk_dollars REAL NOT NULL,
    thesis_allowed INTEGER NOT NULL,
    thesis_rejection_reason TEXT,
    correlated_positions_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station TEXT NOT NULL,
    target_date TEXT NOT NULL,
    ts_utc TEXT NOT NULL,
    lead_time_minutes INTEGER,
    bracket TEXT NOT NULL,
    raw_model_probability REAL,
    calibrated_model_probability REAL,
    market_probability REAL,
    final_trade_probability REAL,
    outcome INTEGER,
    profile TEXT,
    model_state_json TEXT
);
