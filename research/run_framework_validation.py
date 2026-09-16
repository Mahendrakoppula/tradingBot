"""Runs walk-forward validation + parameter sensitivity (Stage 4) on a
hand-picked list of (strategy, symbol) pairs - meant for the top
performers surfaced by research/run_framework_backtest.py's raw,
un-validated backtest, to see whether any of them survive the actual
gate (out-of-sample performance, and not collapsing under a small
parameter nudge) rather than just looking good on the exact history they
were screened against.

Usage: python research/run_framework_validation.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.backtest_technical import load_candles_with_volume  # noqa: E402
from research.framework.backtest_engine import BacktestConfig  # noqa: E402
from research.framework.sensitivity import run_sensitivity  # noqa: E402
from research.framework.walk_forward import ParameterGrid, run_walk_forward  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"

# The top 5 symbols by per-symbol total_return_pct from the full-universe
# raw backtest run 2026-09-10 (research/run_framework_backtest.py,
# equity_delivery scope, default BacktestConfig) - see that run's own
# "best 5 by return%" output per strategy.
BEST_PERFORMERS = {
    "trend_following": ["BSE", "LUPIN", "COCHINSHIP", "FORCEMOT", "HDFCAMC"],
    "breakout": ["ADANIPOWER", "HCLTECH", "UNIONBANK", "COCHINSHIP", "KAYNES"],
    "mean_reversion": ["MOTILALOFS", "ANGELONE", "PATANJALI", "RVNL", "NHPC"],
    "momentum": ["IRFC", "RVNL", "POLYCAB", "COCHINSHIP", "BSE"],
}

GRID = ParameterGrid(min_score=(55.0, 65.0), stop_atr_mult=(1.0, 1.5), reward_risk_ratio=(2.0, 3.0))


def run():
    results = []
    for strategy, symbols in BEST_PERFORMERS.items():
        for symbol in symbols:
            path = DATA_DIR / f"{symbol}_1day.csv"
            if not path.exists():
                print(f"skipping {strategy}/{symbol}: no cached data at {path}")
                continue
            candles = load_candles_with_volume(path)
            n = len(candles)
            train_size, validate_size, test_size = 250, 80, 80
            if n < train_size + validate_size + test_size:
                print(f"skipping {strategy}/{symbol}: only {n} candles, need at least {train_size + validate_size + test_size}")
                continue

            base_config = BacktestConfig(min_history_bars=100, starting_capital=100_000.0, risk_pct_per_trade=1.0, lot_size=1)

            wf = run_walk_forward(candles, symbol, "equity_delivery", strategy, base_config, GRID, train_size, validate_size, test_size)
            agg = wf.aggregate_test_metrics(base_config.starting_capital)

            sens = run_sensitivity(candles, symbol, "equity_delivery", strategy, base_config)

            print(f"=== {strategy} / {symbol} ({n} candles) ===")
            print(f"  walk-forward: {len(wf.folds)} folds, aggregate OOS trades={agg.trade_count}, "
                  f"win_rate={_fmt(agg.win_rate)}, profit_factor={_fmt(agg.profit_factor)}, "
                  f"total_net_pnl={_fmt(agg.total_net_pnl)}, total_return_pct={_fmt(agg.total_return_pct)}")
            print(f"  sensitivity: baseline_net_pnl={_fmt(sens.baseline_net_pnl)}, fragile_parameters={sens.fragile_parameters}")
            print()

            results.append({"strategy": strategy, "symbol": symbol, "walk_forward": wf, "aggregate": agg, "sensitivity": sens})

    return results


def _fmt(x, nd=2):
    return "-" if x is None else (f"{x:.{nd}f}" if x not in (float("inf"), float("-inf")) else str(x))


if __name__ == "__main__":
    run()
