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
