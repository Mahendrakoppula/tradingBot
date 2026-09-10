"""Performance attribution - breaks a backtest's trades down by which
strategy generated them and which regime they were entered in. Exists
to turn "the walk-forward result is inconsistent" (backtesting/BACKTESTS.md
Run 002R) into a testable, mechanism-level hypothesis (e.g. "losses are
concentrated in one strategy" or "one regime") rather than staying a
vague, unexplained observation - and to make that kind of investigation
a repeatable, non-ad-hoc thing to run again on future backtests.
"""
from backtesting.metrics import PerformanceSummary, summarize
from backtesting.trade_record import Trade


def breakdown_by_strategy(trades: list[Trade]) -> dict[str, PerformanceSummary]:
    names = sorted({t.strategy_name for t in trades})
    return {name: summarize([t for t in trades if t.strategy_name == name]) for name in names}


def breakdown_by_regime(trades: list[Trade]) -> dict[str, PerformanceSummary]:
    regimes = sorted({t.entry_regime for t in trades})
    return {regime: summarize([t for t in trades if t.entry_regime == regime]) for regime in regimes}
