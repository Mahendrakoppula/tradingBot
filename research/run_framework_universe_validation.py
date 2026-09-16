"""Runs walk-forward validation + parameter sensitivity (Stage 4) for ONE
strategy across a RANDOM sample of the cached F&O stock universe - the
deliberate counterpart to run_framework_validation.py's hand-picked
"top 5 by in-sample return" list, which is survivorship-biased by
construction (those symbols were chosen BECAUSE they looked good). A
random sample has no such bias: if a strategy still looks decent here,
that's a real signal about the strategy, not about which symbols got
picked.

Usage: python research/run_framework_universe_validation.py [strategy] [n_symbols] [seed]
Defaults: strategy=breakout, n_symbols=30, seed=20260910
"""
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.backtest_technical import load_candles_with_volume  # noqa: E402
from research.framework.backtest_engine import BacktestConfig  # noqa: E402
from research.framework.metrics import compute_metrics  # noqa: E402
from research.framework.sensitivity import run_sensitivity  # noqa: E402
from research.framework.walk_forward import ParameterGrid, run_walk_forward  # noqa: E402
from research.run_framework_backtest import EXCLUDED_UNDERLYINGS  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
GRID = ParameterGrid(min_score=(55.0, 65.0), stop_atr_mult=(1.0, 1.5), reward_risk_ratio=(2.0, 3.0))


def universe_symbols() -> list[str]:
    symbols = []
    for path in sorted(DATA_DIR.glob("*_1day.csv")):
        name = path.stem[: -len("_1day")]
        if name in EXCLUDED_UNDERLYINGS:
            continue
        symbols.append(name)
    return symbols


def run(strategy: str = "breakout", n_symbols: int = 30, seed: int = 20260910):
    all_symbols = universe_symbols()
    rng = random.Random(seed)
    sample = rng.sample(all_symbols, min(n_symbols, len(all_symbols)))
    print(f"strategy={strategy}  random sample: {len(sample)}/{len(all_symbols)} symbols (seed={seed})")
    print(sample)
    print()

    base_config = BacktestConfig(min_history_bars=100, starting_capital=100_000.0, risk_pct_per_trade=1.0, lot_size=1)
    train_size, validate_size, test_size = 250, 80, 80

    all_oos_trades = []
    per_symbol_returns = []  # (symbol, oos_trade_count, oos_return_pct)
    fragile_counts = {}
    skipped = 0

    for i, symbol in enumerate(sample, 1):
        candles = load_candles_with_volume(DATA_DIR / f"{symbol}_1day.csv")
        if len(candles) < train_size + validate_size + test_size:
            skipped += 1
            continue

        wf = run_walk_forward(candles, symbol, "equity_delivery", strategy, base_config, GRID, train_size, validate_size, test_size)
        agg = wf.aggregate_test_metrics(base_config.starting_capital)
        all_oos_trades.extend(t for fr in wf.folds for t in fr.test_trades)
        per_symbol_returns.append((symbol, agg.trade_count, agg.total_return_pct))

        sens = run_sensitivity(candles, symbol, "equity_delivery", strategy, base_config)
        for p in sens.fragile_parameters:
            fragile_counts[p] = fragile_counts.get(p, 0) + 1

        print(f"  [{i}/{len(sample)}] {symbol}: OOS trades={agg.trade_count}, win_rate={_fmt(agg.win_rate)}, "
              f"profit_factor={_fmt(agg.profit_factor)}, return_pct={_fmt(agg.total_return_pct)}, "
              f"fragile={sens.fragile_parameters}")

    if skipped:
        print(f"\nskipped {skipped} symbols with too little history")

    print()
    print("=== AGGREGATE (pooled OOS trades - win_rate/profit_factor/expectancy/avg_r only, see honesty note) ===")
    m = compute_metrics(all_oos_trades, base_config.starting_capital)
    print(f"pooled OOS trades: {m.trade_count}  win_rate: {_fmt(m.win_rate)}  profit_factor: {_fmt(m.profit_factor)}  "
          f"expectancy/trade: Rs.{_fmt(m.expectancy)}  avg_r_multiple: {_fmt(m.avg_r_multiple, 3)}")

    symbols_with_trades = [(s, c, r) for s, c, r in per_symbol_returns if c > 0]
    if symbols_with_trades:
        returns = [r for _, _, r in symbols_with_trades]
        profitable = sum(1 for r in returns if r > 0)
        print(f"symbols with >=1 OOS trade: {len(symbols_with_trades)}/{len(sample) - skipped}")
        print(f"per-symbol OOS return_pct: mean={statistics.mean(returns):.2f}%  median={statistics.median(returns):.2f}%  "
              f"profitable_symbols={profitable}/{len(symbols_with_trades)}")

    print(f"fragile-parameter counts across sample: {fragile_counts}")

    return {"per_symbol": per_symbol_returns, "pooled_metrics": m, "fragile_counts": fragile_counts}


def _fmt(x, nd=2):
    return "-" if x is None else (f"{x:.{nd}f}" if x not in (float("inf"), float("-inf")) else str(x))


if __name__ == "__main__":
    strategy = sys.argv[1] if len(sys.argv) > 1 else "breakout"
    n_symbols = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 20260910
    run(strategy, n_symbols, seed)
