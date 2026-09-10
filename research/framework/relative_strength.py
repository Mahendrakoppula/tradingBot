"""Cross-sectional relative strength: how a stock has performed relative
to a BENCHMARK (e.g. NIFTY) over a lookback window, not relative to its
own history - a genuinely different signal class from every other module
in this framework, all of which judge an instrument purely against its own
past price action (trend, momentum, structure, volatility). "Trade the
leaders, not the laggards" is a well-established, decades-old practice
(IBD's Relative Strength Rating is the best-known formalization) - not a
first-cut guess, though the specific lookback window here IS a first-cut,
same caveat class as every other unvalidated threshold in this project.
"""


def align_benchmark_closes(candles: list[dict], benchmark_candles: list[dict]) -> list[float | None]:
    """Index-aligned to `candles`: each bar's benchmark close on the SAME
    calendar date, or None if the benchmark has no candle for that exact
    date (rare - a stock-specific trading day the benchmark didn't share,
    e.g. around a listing/suspension)."""
    benchmark_by_date = {c["date"]: c["close"] for c in benchmark_candles}
    return [benchmark_by_date.get(c["date"]) for c in candles]


def excess_return_line(candles: list[dict], aligned_benchmark_closes: list[float | None], lookback: int) -> list[float | None]:
    """Index-aligned: value at i is the stock's own simple return over the
    last `lookback` bars minus the benchmark's return over the SAME bars
    (using the date-aligned benchmark close series) - positive means the
    stock outperformed the benchmark over that window, negative means it
    underperformed. None wherever either return can't be computed
    (insufficient history, a zero-price edge case, or a benchmark date
    gap)."""
    n = len(candles)
    result: list[float | None] = [None] * n
    for i in range(lookback, n):
        stock_now, stock_then = candles[i]["close"], candles[i - lookback]["close"]
        bench_now, bench_then = aligned_benchmark_closes[i], aligned_benchmark_closes[i - lookback]
        if not stock_then or bench_then is None or not bench_then or bench_now is None:
            continue
        stock_ret = (stock_now - stock_then) / stock_then
        bench_ret = (bench_now - bench_then) / bench_then
        result[i] = stock_ret - bench_ret
    return result
