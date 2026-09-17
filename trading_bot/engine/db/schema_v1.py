"""Schema v1 (spec §59-§65). Each entry is (version, sql); `Database.migrate()`
applies the ones not yet recorded in `schema_migrations`, in order, each in
its own transaction. NEVER edit an applied version - append a new one.

Tables populated in M1: runs, config_versions, candles, context_snapshots,
presignal_events, signals (status='would_be'). DDL-only until M2:
strategy_versions, option_chain_snapshots, risk_decisions, executions,
trade_results, kill_switch_events.
"""

V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS config_versions (
    id              bigserial PRIMARY KEY,
    hash            text NOT NULL UNIQUE,
    payload         jsonb NOT NULL,
    engine_version  text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
    run_id              uuid PRIMARY KEY,
    mode                text NOT NULL,
    started_at          timestamptz NOT NULL,
    ended_at            timestamptz,
    git_sha             text,
    host                text,
    config_version_id   bigint REFERENCES config_versions(id),
    status              text NOT NULL DEFAULT 'running',
    notes               jsonb
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    id          bigserial PRIMARY KEY,
    strategy    text NOT NULL,
    version     text NOT NULL,
    params      jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (strategy, version)
);

CREATE TABLE IF NOT EXISTS candles (
    token       text NOT NULL,
    exchange    text NOT NULL,
    underlying  text NOT NULL,
    tf          text NOT NULL,
    ts          timestamptz NOT NULL,
    open        numeric(12,2) NOT NULL,
    high        numeric(12,2) NOT NULL,
    low         numeric(12,2) NOT NULL,
    close       numeric(12,2) NOT NULL,
    volume      bigint NOT NULL DEFAULT 0,
    oi          bigint,
    tick_count  integer NOT NULL DEFAULT 0,
    max_gap_ms  integer NOT NULL DEFAULT 0,
    complete    boolean NOT NULL DEFAULT true,
    source      text NOT NULL,
    run_id      uuid REFERENCES runs(run_id),
    PRIMARY KEY (token, tf, ts)
);
CREATE INDEX IF NOT EXISTS candles_underlying_tf_ts ON candles (underlying, tf, ts);

CREATE TABLE IF NOT EXISTS option_chain_snapshots (
    id          bigserial PRIMARY KEY,
    run_id      uuid REFERENCES runs(run_id),
    ts          timestamptz NOT NULL,
    underlying  text NOT NULL,
    expiry      date NOT NULL,
    strike      numeric(12,2) NOT NULL,
    option_type text NOT NULL,
    token       text NOT NULL,
    ltp         numeric(12,2),
    bid         numeric(12,2),
    ask         numeric(12,2),
    bid_qty     integer,
    ask_qty     integer,
    volume      bigint,
    oi          bigint,
    iv          numeric(10,4),
    delta       numeric(10,4),
    gamma       numeric(10,6),
    theta       numeric(10,4),
    vega        numeric(10,4),
    spot        numeric(12,2),
    quality     text
);
CREATE INDEX IF NOT EXISTS ocs_underlying_ts ON option_chain_snapshots (underlying, ts);

CREATE TABLE IF NOT EXISTS context_snapshots (
    id                  bigserial PRIMARY KEY,
    run_id              uuid NOT NULL REFERENCES runs(run_id),
    ts                  timestamptz NOT NULL,
    underlying          text NOT NULL,
    trigger_tf          text NOT NULL,
    spot                numeric(12,2) NOT NULL,
    session_phase       text NOT NULL,
    quality             text NOT NULL,
    bar_index           integer NOT NULL,
    trends              jsonb NOT NULL DEFAULT '{}'::jsonb,
    alignment           jsonb NOT NULL DEFAULT '{}'::jsonb,
    regime              jsonb NOT NULL DEFAULT '{}'::jsonb,
    structure           jsonb NOT NULL DEFAULT '{}'::jsonb,
    levels              jsonb NOT NULL DEFAULT '{}'::jsonb,
    price_action        jsonb NOT NULL DEFAULT '{}'::jsonb,
    indicators          jsonb NOT NULL DEFAULT '{}'::jsonb,
    volume              jsonb NOT NULL DEFAULT '{}'::jsonb,
    config_version_id   bigint REFERENCES config_versions(id)
);
CREATE INDEX IF NOT EXISTS ctx_run_underlying_ts ON context_snapshots (run_id, underlying, ts);

CREATE TABLE IF NOT EXISTS presignal_events (
    id                  bigserial PRIMARY KEY,
    run_id              uuid NOT NULL REFERENCES runs(run_id),
    ts                  timestamptz NOT NULL,
    underlying          text NOT NULL,
    setup_id            uuid NOT NULL,
    direction           text NOT NULL,
    from_stage          text NOT NULL,
    to_stage            text NOT NULL,
    confidence          numeric(6,3) NOT NULL,
    reason_code         text NOT NULL,
    bar_index           integer NOT NULL,
    details             jsonb NOT NULL DEFAULT '{}'::jsonb,
    context_snapshot_id bigint REFERENCES context_snapshots(id)
);
CREATE INDEX IF NOT EXISTS pse_run_setup ON presignal_events (run_id, setup_id);
CREATE INDEX IF NOT EXISTS pse_run_ts ON presignal_events (run_id, ts);

CREATE TABLE IF NOT EXISTS signals (
    signal_id           uuid PRIMARY KEY,
    setup_id            uuid,
    run_id              uuid NOT NULL REFERENCES runs(run_id),
    ts                  timestamptz NOT NULL,
    mode                text NOT NULL,
    underlying          text NOT NULL,
    direction           text NOT NULL,
    option_type         text,
    strategy            text,
    strategy_version    text,
    stage               text NOT NULL,
    score               numeric(6,2),
    status              text NOT NULL,
    reason_code         text,
    explanation         jsonb NOT NULL DEFAULT '{}'::jsonb,
    snapshot            jsonb NOT NULL DEFAULT '{}'::jsonb,
    context_snapshot_id bigint REFERENCES context_snapshots(id),
    CONSTRAINT signals_status_chk CHECK (status IN
        ('would_be','valid','rejected','expired','invalidated','risk_rejected','option_rejected'))
);
CREATE INDEX IF NOT EXISTS signals_run_ts ON signals (run_id, ts);

CREATE TABLE IF NOT EXISTS risk_decisions (
    id              bigserial PRIMARY KEY,
    run_id          uuid NOT NULL REFERENCES runs(run_id),
    signal_id       uuid REFERENCES signals(signal_id),
    ts              timestamptz NOT NULL,
    decision        text NOT NULL,
    reason_code     text,
    risk_amount     numeric(12,2),
    quantity        integer,
    all_in_cost     numeric(12,2),
    expected_value  numeric(12,2),
    details         jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS executions (
    id                  bigserial PRIMARY KEY,
    run_id              uuid NOT NULL REFERENCES runs(run_id),
    signal_id           uuid REFERENCES signals(signal_id),
    ts                  timestamptz NOT NULL,
    mode                text NOT NULL,
    side                text NOT NULL,
    broker_order_id     text,
    unique_order_id     text,
    ordertag            text,
    state               text NOT NULL,
    requested_price     numeric(12,2),
    fill_price          numeric(12,2),
    quantity            integer,
    filled_quantity     integer,
    latency_ms          integer,
    slippage            numeric(12,2),
    details             jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS executions_run_signal ON executions (run_id, signal_id);

CREATE TABLE IF NOT EXISTS trade_results (
    id              bigserial PRIMARY KEY,
    run_id          uuid NOT NULL REFERENCES runs(run_id),
    signal_id       uuid REFERENCES signals(signal_id),
    entry_ts        timestamptz,
    exit_ts         timestamptz,
    entry_price     numeric(12,2),
    exit_price      numeric(12,2),
    quantity        integer,
    gross_pnl       numeric(12,2),
    costs           numeric(12,2),
    net_pnl         numeric(12,2),
    r_multiple      numeric(8,3),
    exit_reason     text,
    mae             numeric(12,2),
    mfe             numeric(12,2),
    details         jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS kill_switch_events (
    id          bigserial PRIMARY KEY,
    run_id      uuid NOT NULL REFERENCES runs(run_id),
    ts          timestamptz NOT NULL,
    switch      text NOT NULL,
    action      text NOT NULL,
    reason      text,
    details     jsonb NOT NULL DEFAULT '{}'::jsonb
);
"""

MIGRATIONS: list[tuple[int, str]] = [(1, V1)]
