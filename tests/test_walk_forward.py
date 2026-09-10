"""requires_real_data tests need data/raw/NIFTY/ONE_DAY.parquet (see
tests/test_feature_engineering.py's module docstring for why)."""
import pytest

from backtesting.event_loop import BacktestConfig
from backtesting.walk_forward import aggregate, generate_windows, walk_forward_backtest
from data.storage import load_ohlcv

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")


def test_generate_windows_are_non_overlapping_and_ordered():
    windows = generate_windows(n=1000, n_windows=4, embargo_bars=5)
    assert len(windows) == 4
    for (start, end), (next_start, _) in zip(windows, windows[1:]):
        assert end <= next_start
        assert next_start - end == 5  # exact embargo gap


def test_generate_windows_covers_as_much_as_divides_evenly():
    windows = generate_windows(n=100, n_windows=2, embargo_bars=0)
    assert windows == [(0, 50), (50, 100)]


def test_generate_windows_rejects_zero_or_negative_n_windows():
    with pytest.raises(ValueError):
        generate_windows(1000, n_windows=0)


def test_generate_windows_rejects_when_not_enough_bars():
    with pytest.raises(ValueError):
        generate_windows(n=10, n_windows=5, embargo_bars=5)


@requires_real_data
def test_walk_forward_backtest_produces_one_summary_per_window():
    result = walk_forward_backtest(NIFTY_DAILY, BacktestConfig(warmup_bars=30), n_windows=5, embargo_bars=5)
    assert len(result.window_summaries) == 5
    assert len(result.windows) == 5


@requires_real_data
def test_walk_forward_folds_are_independent_of_each_other():
    """A fold's trades must only ever reference bars within that fold's
    own local window - if state leaked between folds, this would be the
    first place a bug like that would show up (e.g. a stale open
    position or daily-risk state carrying over)."""
    result = walk_forward_backtest(NIFTY_DAILY, BacktestConfig(warmup_bars=30), n_windows=5, embargo_bars=5)
    for (start, end), summary in zip(result.windows, result.window_summaries):
        window_length = end - start
        # every summary's trade count must be achievable within a window
        # of this length alone - a loose but real sanity bound.
        assert summary.n_trades <= window_length


@requires_real_data
def test_aggregate_reports_sample_sizes_alongside_any_rate():
    result = walk_forward_backtest(NIFTY_DAILY, BacktestConfig(warmup_bars=30), n_windows=5, embargo_bars=5)
    agg = aggregate(result)
    assert agg.n_folds == 5
    assert agg.total_trades == sum(s.n_trades for s in result.window_summaries)
    assert agg.n_folds_profitable <= agg.n_folds


def test_aggregate_handles_zero_trades_across_all_folds_without_crashing():
    from backtesting.metrics import PerformanceSummary
    from backtesting.walk_forward import WalkForwardResult
    empty_summary = PerformanceSummary(0, 0, 0, None, 0.0, None, 0.0)
    result = WalkForwardResult(windows=[(0, 10), (10, 20)], window_summaries=[empty_summary, empty_summary])
    agg = aggregate(result)
    assert agg.mean_win_rate is None
    assert agg.mean_total_pnl == 0.0
    assert agg.n_folds_with_trades == 0
