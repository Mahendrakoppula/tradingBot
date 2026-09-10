from data.quality import check_ohlcv

GOOD_ROWS = [
    {"timestamp": "2026-01-01T09:15:00+05:30", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
    {"timestamp": "2026-01-01T09:16:00+05:30", "open": 100.5, "high": 102, "low": 100, "close": 101, "volume": 1200},
    {"timestamp": "2026-01-01T09:17:00+05:30", "open": 101, "high": 101.5, "low": 100.8, "close": 101.2, "volume": 900},
]


def test_clean_data_has_no_errors():
    report = check_ohlcv(GOOD_ROWS, "NIFTY", "ONE_MINUTE")
    assert report.is_clean
    assert report.errors == []
    assert report.total_rows == 3


def test_empty_rows_is_an_error():
    report = check_ohlcv([], "NIFTY", "ONE_MINUTE")
    assert not report.is_clean
    assert report.errors[0].code == "empty"


def test_duplicate_timestamp_is_an_error():
    rows = GOOD_ROWS + [GOOD_ROWS[-1]]
    report = check_ohlcv(rows, "NIFTY", "ONE_MINUTE")
    assert not report.is_clean
    assert any(i.code == "duplicate_timestamp" for i in report.errors)


def test_non_monotonic_timestamp_is_an_error():
    rows = [GOOD_ROWS[1], GOOD_ROWS[0], GOOD_ROWS[2]]
    report = check_ohlcv(rows, "NIFTY", "ONE_MINUTE")
    assert not report.is_clean
    assert any(i.code == "non_monotonic" for i in report.errors)


def test_missing_field_is_an_error():
    rows = [{**GOOD_ROWS[0], "close": None}]
    report = check_ohlcv(rows, "NIFTY", "ONE_MINUTE")
    assert not report.is_clean
    assert any(i.code == "missing_field" for i in report.errors)


def test_non_positive_price_is_an_error():
    rows = [{**GOOD_ROWS[0], "low": 0}]
    report = check_ohlcv(rows, "NIFTY", "ONE_MINUTE")
    assert not report.is_clean
    assert any(i.code == "non_positive_price" for i in report.errors)


def test_negative_volume_is_an_error():
    rows = [{**GOOD_ROWS[0], "volume": -5}]
    report = check_ohlcv(rows, "NIFTY", "ONE_MINUTE")
    assert not report.is_clean
    assert any(i.code == "negative_volume" for i in report.errors)


def test_low_above_high_is_an_error():
    rows = [{**GOOD_ROWS[0], "low": 200, "high": 100}]
    report = check_ohlcv(rows, "NIFTY", "ONE_MINUTE")
    assert not report.is_clean
    assert any(i.code == "low_above_high" for i in report.errors)


def test_open_outside_low_high_range_is_an_error():
    rows = [{**GOOD_ROWS[0], "open": 500}]
    report = check_ohlcv(rows, "NIFTY", "ONE_MINUTE")
    assert not report.is_clean
    assert any(i.code == "open_out_of_range" for i in report.errors)


def test_large_move_is_a_warning_not_an_error():
    rows = [
        GOOD_ROWS[0],
        {"timestamp": "2026-01-01T09:16:00+05:30", "open": 150, "high": 151, "low": 149, "close": 150, "volume": 1000},
    ]
    report = check_ohlcv(rows, "NIFTY", "ONE_MINUTE")
    assert report.is_clean  # warnings don't block use
    assert any(i.code == "outlier_move" for i in report.warnings)
