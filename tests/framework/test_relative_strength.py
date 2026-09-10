import datetime as dt

from research.framework.relative_strength import align_benchmark_closes, excess_return_line


def _candle(date, close):
    return {"date": date, "close": close}


def test_align_benchmark_closes_matches_by_date():
    d0 = dt.date(2024, 1, 1)
    stock = [_candle(d0, 10.0), _candle(d0 + dt.timedelta(days=1), 11.0)]
    benchmark = [_candle(d0, 100.0), _candle(d0 + dt.timedelta(days=1), 102.0)]
    aligned = align_benchmark_closes(stock, benchmark)
    assert aligned == [100.0, 102.0]


def test_align_benchmark_closes_missing_date_is_none():
    d0 = dt.date(2024, 1, 1)
    stock = [_candle(d0, 10.0), _candle(d0 + dt.timedelta(days=5), 11.0)]  # benchmark has no candle for day+5
    benchmark = [_candle(d0, 100.0)]
    aligned = align_benchmark_closes(stock, benchmark)
    assert aligned == [100.0, None]


def test_excess_return_positive_when_stock_outperforms():
    d0 = dt.date(2024, 1, 1)
    n = 25
    stock = [_candle(d0 + dt.timedelta(days=i), 100.0 * (1.02 ** i)) for i in range(n)]  # +2%/bar
    benchmark_closes = [100.0 * (1.005 ** i) for i in range(n)]  # +0.5%/bar - stock outperforms
    line = excess_return_line(stock, benchmark_closes, lookback=10)
    assert line[:10] == [None] * 10
    assert line[10] is not None
    assert line[10] > 0
    assert line[20] > line[10]  # outperformance compounds further out


def test_excess_return_negative_when_stock_underperforms():
    d0 = dt.date(2024, 1, 1)
    n = 25
    stock = [_candle(d0 + dt.timedelta(days=i), 100.0 * (1.002 ** i)) for i in range(n)]
    benchmark_closes = [100.0 * (1.02 ** i) for i in range(n)]
    line = excess_return_line(stock, benchmark_closes, lookback=10)
    assert line[10] < 0


def test_excess_return_none_when_benchmark_data_missing():
    d0 = dt.date(2024, 1, 1)
    stock = [_candle(d0 + dt.timedelta(days=i), 100.0 + i) for i in range(15)]
    benchmark_closes = [None] * 15
    line = excess_return_line(stock, benchmark_closes, lookback=10)
    assert all(v is None for v in line)
