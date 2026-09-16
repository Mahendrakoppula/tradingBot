"""Performance metrics for a closed-trade log
(research.framework.backtest_engine.ClosedTrade, or anything with equivalent
net_pnl/r_multiple/direction/entry_regime_trend/entry_regime_volatility/
strategy fields): win rate, profit factor, expectancy, average R-multiple,
Sharpe/Sortino-style ratios, max drawdown, and a Calmar-style ratio - each
computable overall (compute_metrics) or broken down by direction, regime,
or strategy/setup (breakdown_by / the three named convenience wrappers).

TRADE-LEVEL, not bar-level: the Sharpe/Sortino/Calmar-style ratios are
computed over the sequence of PER-TRADE returns (net_pnl / starting_capital)
- the standard convention for a discretionary/signal-based strategy where
trades don't land on a fixed daily grid - and are deliberately NOT
annualized here, since nothing in this module tracks wall-clock time
between trades. A caller wanting an annualized figure should scale by
their own known trade frequency; that's why these are named *_like rather
than claimed as the textbook annualized versions.

A "by timeframe" breakdown (the master-prompt spec's own requirement) has
no natural convenience wrapper yet because research.framework.backtest_engine
is daily-bar-only in Stage 3 - once a multi-timeframe backtest exists,
breakdown_by(trades, capital, lambda t: t.timeframe) covers it with no
change needed here.
"""
import math
from dataclasses import dataclass


@dataclass
class Metrics:
    trade_count: int
    win_rate: float | None
    profit_factor: float | None
    expectancy: float
    avg_r_multiple: float | None
    sharpe_like: float | None
    sortino_like: float | None
    max_drawdown_pct: float | None
    calmar_like: float | None
    total_net_pnl: float
    total_return_pct: float


def _stdev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def compute_metrics(trades: list, starting_capital: float) -> Metrics:
    n = len(trades)
    if n == 0:
        return Metrics(0, None, None, 0.0, None, None, None, None, None, 0.0, 0.0)

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    win_rate = len(wins) / n
    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = -sum(t.net_pnl for t in losses)
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = None

    total_net_pnl = sum(t.net_pnl for t in trades)
    expectancy = total_net_pnl / n

    r_multiples = [t.r_multiple for t in trades if t.r_multiple is not None]
    avg_r_multiple = sum(r_multiples) / len(r_multiples) if r_multiples else None

    returns = [t.net_pnl / starting_capital for t in trades] if starting_capital else [0.0] * n
    mean_return = sum(returns) / n
    stdev_return = _stdev(returns)
    sharpe_like = (mean_return / stdev_return) if stdev_return > 0 else None

    downside = [r for r in returns if r < 0]
    if len(downside) >= 2:
        downside_stdev = _stdev(downside)
    elif len(downside) == 1:
        downside_stdev = abs(downside[0])
    else:
        downside_stdev = 0.0
    sortino_like = (mean_return / downside_stdev) if downside_stdev > 0 else None

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cum += t.net_pnl
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    max_drawdown_pct = (max_dd / starting_capital * 100.0) if starting_capital else None

    total_return_pct = (total_net_pnl / starting_capital * 100.0) if starting_capital else 0.0
    calmar_like = (total_return_pct / max_drawdown_pct) if max_drawdown_pct and max_drawdown_pct > 0 else None

    return Metrics(
        trade_count=n, win_rate=win_rate, profit_factor=profit_factor, expectancy=expectancy,
        avg_r_multiple=avg_r_multiple, sharpe_like=sharpe_like, sortino_like=sortino_like,
        max_drawdown_pct=max_drawdown_pct, calmar_like=calmar_like,
        total_net_pnl=total_net_pnl, total_return_pct=total_return_pct,
    )


def breakdown_by(trades: list, starting_capital: float, key_fn) -> dict:
    groups: dict = {}
    for t in trades:
        groups.setdefault(key_fn(t), []).append(t)
    return {key: compute_metrics(group_trades, starting_capital) for key, group_trades in groups.items()}


def breakdown_by_direction(trades: list, starting_capital: float) -> dict:
    return breakdown_by(trades, starting_capital, lambda t: t.direction)


def breakdown_by_regime(trades: list, starting_capital: float) -> dict:
    return breakdown_by(trades, starting_capital, lambda t: (t.entry_regime_trend, t.entry_regime_volatility))


def breakdown_by_strategy(trades: list, starting_capital: float) -> dict:
    return breakdown_by(trades, starting_capital, lambda t: t.strategy)
