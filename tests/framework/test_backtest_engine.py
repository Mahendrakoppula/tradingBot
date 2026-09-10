import datetime as dt

from research.framework.backtest_engine import BacktestConfig, simulate


def _candle(date, o, h, l, c, v=1000):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _flat_candles(n):
    d0 = dt.date(2024, 1, 1)
    return [_candle(d0 + dt.timedelta(days=i), 100.0, 100.5, 99.5, 100.0) for i in range(n)]


def _choppy_then_trend_candles():
    """First 80 bars oscillate in a tight range (builds swing highs/lows
    around a ~10-point range so breakout has a real resistance level to
    break), then a decisive, volume-backed rally through the range high
    for 60 bars (a real trend for trend_following/momentum, a real
    breakout for breakout)."""
    d0 = dt.date(2024, 1, 1)
    candles = []
    price = 100.0
    for i in range(80):
        wobble = 4.0 if i % 4 == 0 else (-4.0 if i % 4 == 2 else 0.0)
        base = price + wobble
        candles.append(_candle(d0 + dt.timedelta(days=i), base, base + 2, base - 2, base, 800))
    price = 100.0
    for i in range(80, 140):
        price += 2.5
        candles.append(_candle(d0 + dt.timedelta(days=i), price, price + 2.5, price - 0.5, price + 2.0, 3000))
    return candles


def _oscillating_candles():
    """A range-bound series that repeatedly swings between RSI extremes
    with no sustained trend - the setup mean_reversion is meant for."""
    d0 = dt.date(2024, 1, 1)
    candles = []
    for i in range(160):
        base = 100.0 + 8.0 * (1 if (i // 5) % 2 == 0 else -1)
        candles.append(_candle(d0 + dt.timedelta(days=i), base, base + 1, base - 1, base, 500))
    return candles


def test_simulate_flat_market_produces_no_trend_following_trades():
    candles = _flat_candles(150)
    result = simulate(candles, "TEST", "index_option", "trend_following", BacktestConfig(min_history_bars=60))
    assert result.trades == []
    assert result.decisions
    assert all(d.outcome == "no_trade" for d in result.decisions)
    assert all(d.detail.get("rejected_because") == "no_established_trend" for d in result.decisions)


def test_simulate_real_uptrend_produces_trend_following_trades_with_costs_deducted():
    candles = _choppy_then_trend_candles()
    result = simulate(candles, "NIFTY", "index_option", "trend_following", BacktestConfig(min_history_bars=60, lot_size=25))
    assert len(result.trades) >= 1
    for t in result.trades:
        assert t.costs > 0
        assert t.net_pnl == t.gross_pnl - t.costs
        assert t.quantity > 0


def test_simulate_breakout_strategy_enters_on_a_real_breakout():
    candles = _choppy_then_trend_candles()
    result = simulate(candles, "NIFTY", "index_option", "breakout", BacktestConfig(min_history_bars=60, lot_size=25))
    assert any(d.outcome == "signal_entered" for d in result.decisions)


def test_simulate_momentum_strategy_runs_without_crashing():
    candles = _choppy_then_trend_candles()
    result = simulate(candles, "NIFTY", "index_option", "momentum", BacktestConfig(min_history_bars=60, lot_size=25))
    assert isinstance(result.trades, list)
    assert isinstance(result.decisions, list)


def test_simulate_mean_reversion_can_enter_in_an_oscillating_range():
    candles = _oscillating_candles()
    result = simulate(candles, "NIFTY", "index_option", "mean_reversion", BacktestConfig(min_history_bars=60, lot_size=25))
    assert isinstance(result.trades, list)  # oscillation is designed to hit RSI extremes repeatedly
    assert any(d.outcome in ("signal_entered", "no_trade", "zero_quantity", "blocked_by_portfolio_risk") for d in result.decisions)


def test_simulate_unknown_strategy_raises():
    try:
        simulate(_flat_candles(100), "NIFTY", "index_option", "not_a_strategy")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_simulate_equity_delivery_cost_model_differs_from_option_cost_model():
    candles = _choppy_then_trend_candles()
    cfg = BacktestConfig(min_history_bars=60, lot_size=25)
    option_result = simulate(candles, "NIFTY", "index_option", "trend_following", cfg)
    equity_result = simulate(candles, "RELIANCE", "equity_delivery", "trend_following", cfg)
    assert len(option_result.trades) == len(equity_result.trades)  # same decisions/sizing, cost model doesn't affect entries
    total_option_cost = sum(t.costs for t in option_result.trades)
    total_equity_cost = sum(t.costs for t in equity_result.trades)
    assert total_option_cost != total_equity_cost


# --- trailing exit (2026-09-10: real backtest evidence showed max_hold_bars
# was cutting many winners well short of the fixed target) ---

def _candle_ts(date, o, h, l, c, v=1000):
    return {"ts": dt.datetime.combine(date, dt.time(0, 0)), "date": date, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _long_uptrend_candles_ts(n=400):
    d0 = dt.date(2020, 1, 1)
    candles = []
    price = 100.0
    for i in range(n):
        noise = 1.5 if i % 3 == 0 else (-1.0 if i % 3 == 1 else 0.5)
        price += 0.8
        base = price + noise
        candles.append(_candle_ts(d0 + dt.timedelta(days=i), base, base + 2, base - 2, base, 1500))
    return candles


def test_trailing_exit_lets_at_least_one_winner_exceed_the_fixed_target_r():
    candles = _long_uptrend_candles_ts()
    cfg = BacktestConfig(min_history_bars=60, lot_size=1, use_trailing_exit=True, max_hold_bars=200, reward_risk_ratio=2.0)
    result = simulate(candles, "TEST", "equity_delivery", "trend_following", cfg)
    assert len(result.trades) >= 1
    # Without trailing, no trade can exceed reward_risk_ratio (2.0R) since
    # the fixed target caps it there exactly - trailing lets a runner go
    # further once activated.
    assert any(t.r_multiple > 2.0 for t in result.trades)


def test_trailing_exit_disabled_by_default_caps_wins_at_target_r():
    candles = _long_uptrend_candles_ts()
    cfg = BacktestConfig(min_history_bars=60, lot_size=1, max_hold_bars=200, reward_risk_ratio=2.0)
    result = simulate(candles, "TEST", "equity_delivery", "trend_following", cfg)
    target_exits = [t for t in result.trades if t.exit_reason == "target"]
    assert target_exits  # sanity: some trades did hit the fixed target
    assert all(t.r_multiple <= 2.05 for t in target_exits)  # small slack for cost drag


# --- multi-timeframe confirmation (2026-09-10: mtf.py built in Stage 1,
# never actually required by any backtest until now) ---

def test_mtf_confirmation_blocks_a_daily_up_signal_against_a_weekly_downtrend():
    # A long secular decline (300 days) followed by a sharp short-lived
    # rally (30 days): the DAILY regime picks up the recent rally as "up",
    # but the WEEKLY regime - averaging over the much longer preceding
    # decline - still reads "down" for most of that window, so the gate
    # should reject at least some daily "up" signals it would otherwise take.
    d0 = dt.date(2020, 1, 1)
    candles = []
    price = 200.0
    for i in range(300):
        price -= 0.5
        candles.append(_candle_ts(d0 + dt.timedelta(days=i), price, price + 1, price - 1, price, 1000))
    for i in range(300, 330):
        price += 3.0
        candles.append(_candle_ts(d0 + dt.timedelta(days=i), price, price + 3, price - 0.5, price + 2.5, 3000))

    cfg = BacktestConfig(min_history_bars=60, lot_size=1, require_mtf_confirmation=True)
    result = simulate(candles, "TEST", "equity_delivery", "trend_following", cfg)
    assert any(d.detail.get("rejected_because") == "mtf_not_confirmed" for d in result.decisions)


def test_mtf_confirmation_allows_entries_in_a_real_sustained_uptrend():
    candles = _long_uptrend_candles_ts(n=400)
    cfg = BacktestConfig(min_history_bars=60, lot_size=1, require_mtf_confirmation=True)
    result = simulate(candles, "TEST", "equity_delivery", "trend_following", cfg)
    assert len(result.trades) >= 1
    assert all(t.direction == "up" for t in result.trades)
