"""Runs research.framework.backtest_engine across the full cached F&O stock
universe (research/data/*_1day.csv, excluding the indices) for all 4
strategies, equity-delivery scope - the only scope without the
index-option spot-proxy sizing gap documented in backtest_engine.py's own
module docstring, so the only scope where a run across many names is
currently meaningful.

HONEST AGGREGATION NOTE: this runs each symbol as its OWN independent
backtest, each notionally starting from `starting_capital` - it does NOT
simulate one shared portfolio account trading all 210 names together
(that needs the multi-underlying portfolio loop backtest_engine.py's
docstring already flags as a documented gap, not built yet). Because of
that:
  - win_rate / profit_factor / expectancy / avg_r_multiple are valid to
    pool across every symbol's trades directly (they don't depend on
    which capital base a trade's P&L is measured against).
  - total_return_pct / max_drawdown_pct / Sharpe-like ratios are NOT
    pooled here - blending 210 independent accounts' rupee P&L over one
    capital denominator would overstate returns by ~210x. Instead, each
    symbol's OWN return%/drawdown% (computed against its own capital) is
    reported as a distribution (mean/median across symbols) - "what a
    typical single-symbol account running this strategy would have seen",
    not a portfolio number.

Usage: python research/run_framework_backtest.py
"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.backtest_technical import load_candles_with_volume  # noqa: E402
from research.framework.backtest_engine import STRATEGIES, BacktestConfig, simulate  # noqa: E402
from research.framework.metrics import compute_metrics  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
EXCLUDED_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "SENSEX"}


def universe_symbols() -> list[str]:
    symbols = []
    for path in sorted(DATA_DIR.glob("*_1day.csv")):
        name = path.stem[: -len("_1day")]
        if name in EXCLUDED_UNDERLYINGS:
            continue
        symbols.append(name)
    return symbols


def run(
    strategies=STRATEGIES,
    min_history_bars: int = 100,
    starting_capital: float = 100_000.0,
    risk_pct_per_trade: float = 1.0,
    lot_size: int = 1,
) -> dict:
    symbols = universe_symbols()
    print(f"F&O stock universe: {len(symbols)} symbols cached")
    config = BacktestConfig(
        min_history_bars=min_history_bars, starting_capital=starting_capital,
        risk_pct_per_trade=risk_pct_per_trade, lot_size=lot_size,
    )

    pooled_trades = {s: [] for s in strategies}
    per_symbol_returns = {s: [] for s in strategies}  # (symbol, total_return_pct)
    skipped = 0

    for i, symbol in enumerate(symbols, 1):
        candles = load_candles_with_volume(DATA_DIR / f"{symbol}_1day.csv")
        if len(candles) < min_history_bars + 20:
            skipped += 1
            continue
        for strategy in strategies:
            result = simulate(candles, symbol, "equity_delivery", strategy, config)
            pooled_trades[strategy].extend(result.trades)
            if result.trades:
                m = compute_metrics(result.trades, starting_capital)
                per_symbol_returns[strategy].append((symbol, m.total_return_pct, m.max_drawdown_pct))
        if i % 25 == 0:
            print(f"  ...{i}/{len(symbols)} symbols processed")

    if skipped:
        print(f"skipped {skipped} symbols with too little history for min_history_bars={min_history_bars}")

    print()
    summary = {}
    for strategy in strategies:
        trades = pooled_trades[strategy]
        m = compute_metrics(trades, starting_capital)  # only the capital-independent fields are trustworthy here
        returns = per_symbol_returns[strategy]
        symbols_with_trades = len(returns)
        return_pcts = [r for _, r, _ in returns]
        drawdowns = [d for _, _, d in returns if d is not None]
        profitable_symbols = sum(1 for r in return_pcts if r > 0)

        print(f"=== {strategy} ===")
        print(f"  symbols with >=1 trade: {symbols_with_trades}/{len(symbols)}")
        print(f"  pooled trades: {m.trade_count}  win_rate: {_fmt(m.win_rate)}  profit_factor: {_fmt(m.profit_factor)}  "
              f"expectancy/trade: Rs.{_fmt(m.expectancy)}  avg_r_multiple: {_fmt(m.avg_r_multiple, 3)}")
        if return_pcts:
            print(f"  per-symbol total_return_pct: mean={statistics.mean(return_pcts):.2f}%  "
                  f"median={statistics.median(return_pcts):.2f}%  profitable_symbols={profitable_symbols}/{symbols_with_trades}")
        if drawdowns:
            print(f"  per-symbol max_drawdown_pct: mean={statistics.mean(drawdowns):.2f}%  max={max(drawdowns):.2f}%")
        ranked = sorted(returns, key=lambda t: t[1], reverse=True)
        print(f"  best 5 by return%: {[(s, round(r, 1)) for s, r, _ in ranked[:5]]}")
        print(f"  worst 5 by return%: {[(s, round(r, 1)) for s, r, _ in ranked[-5:]]}")
        print()

        summary[strategy] = {
            "symbols_with_trades": symbols_with_trades,
            "pooled_metrics": m,
            "mean_return_pct": statistics.mean(return_pcts) if return_pcts else None,
            "median_return_pct": statistics.median(return_pcts) if return_pcts else None,
            "profitable_symbols": profitable_symbols,
        }

    return summary


def _fmt(x, nd=3):
    return "-" if x is None else (f"{x:.{nd}f}" if x not in (float("inf"), float("-inf")) else str(x))


if __name__ == "__main__":
    run()
