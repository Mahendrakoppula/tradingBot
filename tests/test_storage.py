from data.storage import load_ohlcv, save_ohlcv

ROWS = [
    {"timestamp": "2026-01-01T09:16:00+05:30", "open": 100.5, "high": 102, "low": 100, "close": 101, "volume": 1200},
    {"timestamp": "2026-01-01T09:15:00+05:30", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
]


def test_save_and_load_round_trip(tmp_path):
    save_ohlcv(ROWS, "NIFTY", "ONE_MINUTE", root=tmp_path)
    df = load_ohlcv("NIFTY", "ONE_MINUTE", root=tmp_path)
    assert len(df) == 2
    # sorted ascending by timestamp despite input order
    assert df.iloc[0]["close"] == 100.5
    assert df.iloc[1]["close"] == 101


def test_save_deduplicates_identical_timestamps(tmp_path):
    save_ohlcv(ROWS + [ROWS[0]], "NIFTY", "ONE_MINUTE", root=tmp_path)
    df = load_ohlcv("NIFTY", "ONE_MINUTE", root=tmp_path)
    assert len(df) == 2


def test_load_missing_symbol_returns_empty_dataframe(tmp_path):
    df = load_ohlcv("NOTHING", "ONE_DAY", root=tmp_path)
    assert len(df) == 0
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


def test_save_overwrites_previous_file(tmp_path):
    save_ohlcv(ROWS, "NIFTY", "ONE_MINUTE", root=tmp_path)
    save_ohlcv([ROWS[0]], "NIFTY", "ONE_MINUTE", root=tmp_path)
    df = load_ohlcv("NIFTY", "ONE_MINUTE", root=tmp_path)
    assert len(df) == 1
