"""Event-driven backtest engine: walks ONE underlying's candle series
bar-by-bar - only candles up to and including the bar being evaluated are
ever visible to a decision at that bar, no vectorized shortcuts - running
one of Stage 2's 4 strategies against Stage 1's regime/structure context,
sized and stopped via Stage 2's risk module, with real round-trip costs
(trading_bot.costs, same functions/rates run_technical.py uses live)
deducted from every closed trade.

SCOPE CAVEAT (inherited as-is from research/backtest_technical.py, not
solved here): for asset_scope "index_option"/"stock_option" this is a
SPOT-PRICE PROXY, not real option economics - no premium, theta, IV, or
strike selection modeled, and the "premium" fed to the cost model is the
spot price itself, so the COST estimate is a proxy too, not just the P&L.
Only "equity_delivery" uses real price data with a real cost model. A
result here is a screen for whether a strategy's ENTRY/EXIT LOGIC has real
signal, never a profit forecast.

DAILY-BAR SCOPE: built and tested against daily OHLCV (the swing/
positional style this framework's regime/structure/MTF machinery actually
suits) - not the live bot's own 1-min scalp/5-min intraday tiers, which
stay that bot's own simpler EMA/VWAP mechanism.

SINGLE-UNDERLYING SCOPE: this walks one underlying at a time - the
correlation-cap piece of risk.correlated_exposure_multiplier needs a
shared multi-underlying portfolio loop this stage does not build (a
documented gap, not silently skipped - see the module docstring's own
Stage 4 handoff note in the plan file). risk.evaluate_portfolio_risk's
daily-loss-limit/drawdown/consecutive-loss gates ARE wired in per
underlying below, since those only need this one underlying's own running
P&L, not cross-underlying visibility.

KNOWN GAP - INDEX-OPTION POSITION SIZING: risk.risk_based_quantity sizes
against the SPOT price's own stop distance (in index POINTS), since that's
all the spot-price-proxy scope has - confirmed live against real NIFTY
data: with a ~174-point ATR and a 1.5x stop multiplier, a 1% risk budget on
Rs.50,000 capital (Rs.500) can't afford even one lot at any real NIFTY lot
size, so index_option/stock_option backtests can size to ZERO trades even
when a strategy is generating real signals (equity_delivery, using real
per-share prices, does not have this problem - confirmed working with real
trades on cached F&O stock data). Fixing this properly needs a real
premium-scaling assumption (e.g. a Black-Scholes overlay, per
research/README.md's own suggested next step) - deliberately NOT
papered over here with an arbitrary scaling guess; callers backtesting the
options scopes today need a much smaller effective stop distance and/or a
larger risk_pct_per_trade/capital than the live bot's own numbers to get
any trades to size at all.

STRUCTURE CONFIRMATION LAG: support_resistance.find_swing_points needs
`right` future candles to confirm a swing point - computed ONCE over the
full series up front, but a swing at index s is only used in a decision at
bar i once s + right <= i (i.e. only once it's actually confirmable from
data available at bar i), closing the lookahead gap flagged in
market_structure.py's own docstring.
"""
from dataclasses import dataclass, field

from research.framework import risk as risk_mod
from research.framework import strategies as strat
from research.framework.market_structure import find_structure_events
from research.framework.regime import Regime, classify_regime
from research.framework.scoring import ScoreWeights, ScoringInputs
from trading_bot import costs as costs_mod
from trading_bot.chart_patterns import detect_breakout
from trading_bot.indicators import macd as calc_macd
from trading_bot.indicators import rolling_avg_volume
from trading_bot.indicators import rsi as calc_rsi
from trading_bot.support_resistance import cluster_levels, find_swing_points
from trading_bot.technical_config import TechnicalConfig
from trading_bot.volume_analysis import relative_volume as calc_relative_volume

STRATEGIES = ("trend_following", "breakout", "mean_reversion", "momentum")


@dataclass
class BacktestConfig:
    starting_capital: float = 50_000.0
    risk_pct_per_trade: float = 1.0
    lot_size: int = 1
    stop_style: str = "atr"  # "atr" | "structure"
    stop_atr_mult: float = 1.5
    reward_risk_ratio: float = 2.0
    max_hold_bars: int = 20
    min_history_bars: int = 60
    min_score: float = 60.0
    structure_left: int = 3
    structure_right: int = 3
    avg_volume_period: int = 20
    weights: ScoreWeights | None = None  # overrides the strategy's own default weight preset when set
    technical_config: TechnicalConfig = field(default_factory=lambda: TechnicalConfig(dry_run=True, enable_trading=False))


@dataclass
class Position:
    direction: str
    entry_index: int
    entry_date: object
    entry_price: float
    stop_price: float
    target_price: float
    quantity: int
    entry_regime: Regime
    bars_held: int = 0


@dataclass
class ClosedTrade:
    underlying: str
    strategy: str
    direction: str
    entry_date: object
    exit_date: object
    entry_price: float
    exit_price: float
    quantity: int
    gross_pnl: float
    costs: float
    net_pnl: float
    exit_reason: str  # "stop" | "target" | "max_hold" | "end_of_data"
    r_multiple: float
    entry_regime_trend: str
    entry_regime_volatility: str


@dataclass
class DecisionRecord:
    underlying: str
    index: int
    date: object
    outcome: str  # "signal_entered" | "no_trade" | "blocked_by_portfolio_risk" | "zero_quantity"
    detail: dict


@dataclass
class BacktestResult:
    underlying: str
    asset_scope: str
    strategy: str
    starting_capital: float
    ending_capital: float
    trades: list  # list[ClosedTrade]
    decisions: list  # list[DecisionRecord]


def _cost_fn(asset_scope: str, cfg: TechnicalConfig):
    if asset_scope == "equity_delivery":
        return lambda entry, exit_, qty: costs_mod.equity_round_trip_cost(
            entry, exit_, qty, cfg.cost_equity_brokerage_per_order, cfg.cost_equity_stt_pct,
            cfg.cost_exchange_txn_pct, cfg.cost_sebi_fee_pct, cfg.cost_equity_stamp_duty_pct, cfg.cost_gst_pct,
        )
    return lambda entry, exit_, qty: costs_mod.option_round_trip_cost(
        entry, exit_, qty, cfg.cost_brokerage_per_order, cfg.cost_stt_sell_pct,
        cfg.cost_exchange_txn_pct, cfg.cost_sebi_fee_pct, cfg.cost_stamp_duty_pct, cfg.cost_gst_pct,
    )


def _check_exit(position: Position, candle: dict) -> tuple[str, float] | None:
    """Stop checked before target when both are touched the same bar - a
    deliberately conservative first-cut convention (this repo has no
    established precedent either way), same caveat class as every other
    unvalidated first-cut threshold here."""
    if position.direction == "up":
        if candle["low"] <= position.stop_price:
            return "stop", position.stop_price
        if candle["high"] >= position.target_price:
            return "target", position.target_price
    else:
        if candle["high"] >= position.stop_price:
            return "stop", position.stop_price
        if candle["low"] <= position.target_price:
            return "target", position.target_price
    return None


def _nearest_breakout_trigger(candles_so_far: list[dict], visible_swings: list) -> tuple[str | None, bool]:
    """Close-based breakout check against the nearest resistance (for "up")
    or support (for "down") level built from confirmed swing points -
    returns (direction, triggered).

    Candidate levels are chosen relative to the PRIOR bar's close, not
    today's: a level still above/below yesterday's close is what was
    acting as resistance/support until today - picking relative to
    TODAY's close instead would only ever select levels the breakout
    can't possibly have just crossed (a level > today's close can never
    satisfy close > level)."""
    if len(candles_so_far) < 2:
        return None, False
    prev_close = candles_so_far[-2]["close"]
    levels = cluster_levels(visible_swings, tolerance_pct=0.5)
    resistances = [lv for lv in levels if lv.kind == "high" and lv.price > prev_close]
    supports = [lv for lv in levels if lv.kind == "low" and lv.price < prev_close]
    if resistances:
        nearest = min(resistances, key=lambda lv: lv.price)
        if detect_breakout(candles_so_far, nearest.price, "up"):
            return "up", True
    if supports:
        nearest = max(supports, key=lambda lv: lv.price)
        if detect_breakout(candles_so_far, nearest.price, "down"):
            return "down", True
    return None, False


def _decide(
    strategy: str, regime: Regime, inputs: ScoringInputs, candles_so_far: list[dict], visible_swings: list,
    min_score: float, weights: ScoreWeights | None = None,
):
    kwargs = {"min_score": min_score}
    if weights is not None:
        kwargs["weights"] = weights
    if strategy == "trend_following":
        return strat.trend_following(regime, inputs, **kwargs)
    if strategy == "mean_reversion":
        return strat.mean_reversion(regime, inputs, **kwargs)
    if strategy == "momentum":
        candidate = regime.trend_direction if regime.trend_direction != "none" else ("up" if (inputs.rsi or 50.0) >= 50.0 else "down")
        return strat.momentum(regime, inputs, candidate, **kwargs)
    if strategy == "breakout":
        direction, triggered = _nearest_breakout_trigger(candles_so_far, visible_swings)
        inputs.entry_trigger_confirmed = triggered
        return strat.breakout(regime, inputs, direction or "up", **kwargs)
    raise ValueError(f"unknown strategy {strategy!r}, expected one of {STRATEGIES}")


def simulate(
    candles: list[dict], underlying: str, asset_scope: str, strategy: str, config: BacktestConfig = None
) -> BacktestResult:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}, expected one of {STRATEGIES}")
    config = config or BacktestConfig()
    cfg = config.technical_config
    cost_fn = _cost_fn(asset_scope, cfg)

    closes = [c["close"] for c in candles]
    rsi_line = calc_rsi(closes)
    _, _, macd_hist_line = calc_macd(closes)
    avg_vol_line = rolling_avg_volume(candles, config.avg_volume_period)
    swings_all = find_swing_points(candles, left=config.structure_left, right=config.structure_right)

    capital = config.starting_capital
    peak_capital = capital
    portfolio_state = risk_mod.PortfolioRiskState(starting_capital=capital, current_capital=capital, peak_capital=capital)

    position: Position | None = None
    trades: list[ClosedTrade] = []
    decisions: list[DecisionRecord] = []

    n = len(candles)
    for i in range(config.min_history_bars, n):
        candle = candles[i]

        if position is not None:
            position.bars_held += 1
            exit_info = _check_exit(position, candle)
            if exit_info is None and position.bars_held >= config.max_hold_bars:
                exit_info = ("max_hold", candle["close"])
            if exit_info is None and i == n - 1:
                exit_info = ("end_of_data", candle["close"])
            if exit_info is not None:
                reason, exit_price = exit_info
                gross_pnl = (exit_price - position.entry_price) * position.quantity if position.direction == "up" else (
                    position.entry_price - exit_price
                ) * position.quantity
                cost = cost_fn(position.entry_price, exit_price, position.quantity)
                net_pnl = gross_pnl - cost
                risk_per_unit = abs(position.entry_price - position.stop_price)
                r_multiple = (net_pnl / (risk_per_unit * position.quantity)) if risk_per_unit > 0 and position.quantity else 0.0

                capital += net_pnl
                peak_capital = max(peak_capital, capital)
                portfolio_state.current_capital = capital
                portfolio_state.peak_capital = peak_capital
                portfolio_state.daily_realized_pnl = net_pnl
                portfolio_state.consecutive_losses = portfolio_state.consecutive_losses + 1 if net_pnl < 0 else 0

                trades.append(ClosedTrade(
                    underlying=underlying, strategy=strategy, direction=position.direction,
                    entry_date=position.entry_date, exit_date=candle.get("date"),
                    entry_price=position.entry_price, exit_price=exit_price, quantity=position.quantity,
                    gross_pnl=gross_pnl, costs=cost, net_pnl=net_pnl, exit_reason=reason, r_multiple=r_multiple,
                    entry_regime_trend=position.entry_regime.trend_direction,
                    entry_regime_volatility=position.entry_regime.volatility_bucket,
                ))
                position = None
            continue

        # Fresh day, no position, so no trade has closed yet today (this
        # engine holds at most one position at a time) - reset daily P&L
        # rather than let it carry forward from whichever earlier day's
        # trade last set it. This makes the daily-loss-limit gate itself
        # inert in this single-underlying, single-trade-per-day engine
        # (there's never a second same-day trade for it to block) - it's
        # exercised properly once a multi-underlying/multi-trade-per-day
        # extension exists (see module docstring's portfolio-scope note).
        portfolio_state.daily_realized_pnl = 0.0

        candles_so_far = candles[: i + 1]
        regime = classify_regime(candles_so_far)
        visible_swings = [s for s in swings_all if s.index + config.structure_right <= i]
        events = find_structure_events(visible_swings)
        rel_vol = calc_relative_volume(candle.get("volume", 0) or 0, avg_vol_line[i]) if avg_vol_line[i] else None

        inputs = ScoringInputs(
            rsi=rsi_line[i], macd_histogram=macd_hist_line[i], relative_volume=rel_vol,
            recent_structure_events=events, entry_trigger_confirmed=False,
        )
        result = _decide(strategy, regime, inputs, candles_so_far, visible_swings, config.min_score, config.weights)

        if not isinstance(result, strat.Signal):
            decisions.append(DecisionRecord(underlying, i, candle.get("date"), "no_trade", result.reason))
            continue

        risk_decision = risk_mod.evaluate_portfolio_risk(portfolio_state)
        if not risk_decision.allowed:
            decisions.append(DecisionRecord(
                underlying, i, candle.get("date"), "blocked_by_portfolio_risk",
                {"strategy": result.strategy, "direction": result.direction, "portfolio_risk_reason": risk_decision.reason},
            ))
            continue

        entry_price = candle["close"]
        atr_value = regime.atr or 0.0
        if config.stop_style == "structure":
            st = risk_mod.structure_stop_target(
                entry_price, result.direction, visible_swings, atr_value,
                reward_risk_ratio=config.reward_risk_ratio,
            )
        else:
            st = risk_mod.atr_stop_target(
                entry_price, result.direction, atr_value,
                stop_atr_mult=config.stop_atr_mult, reward_risk_ratio=config.reward_risk_ratio,
            )

        effective_risk_pct = config.risk_pct_per_trade * risk_decision.risk_multiplier
        quantity = risk_mod.risk_based_quantity(capital, effective_risk_pct, entry_price, st.stop_price, config.lot_size)
        if quantity <= 0:
            decisions.append(DecisionRecord(
                underlying, i, candle.get("date"), "zero_quantity",
                {"strategy": result.strategy, "direction": result.direction, "stop_price": st.stop_price},
            ))
            continue

        position = Position(
            direction=result.direction, entry_index=i, entry_date=candle.get("date"), entry_price=entry_price,
            stop_price=st.stop_price, target_price=st.target_price, quantity=quantity, entry_regime=regime,
        )
        decisions.append(DecisionRecord(
            underlying, i, candle.get("date"), "signal_entered",
            {"strategy": result.strategy, "direction": result.direction, "confidence": result.confidence, "quantity": quantity},
        ))

    return BacktestResult(
        underlying=underlying, asset_scope=asset_scope, strategy=strategy,
        starting_capital=config.starting_capital, ending_capital=capital,
        trades=trades, decisions=decisions,
    )
