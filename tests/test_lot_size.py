import pytest

from data.lot_size import resolve_lot_size

SAMPLE_INSTRUMENTS = [
    {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "lotsize": "65"},
    {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "lotsize": "65"},
    {"name": "BANKNIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "lotsize": "30"},
    {"name": "SENSEX", "exch_seg": "BFO", "instrumenttype": "OPTIDX", "lotsize": "20"},
    {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "FUTIDX", "lotsize": "999"},  # not an option - must be ignored
    {"name": "RELIANCE", "exch_seg": "NFO", "instrumenttype": "OPTSTK", "lotsize": "500"},  # stock option - must be ignored
]


def test_resolve_lot_size_returns_the_consistent_value():
    assert resolve_lot_size(SAMPLE_INSTRUMENTS, "NIFTY") == 65
    assert resolve_lot_size(SAMPLE_INSTRUMENTS, "BANKNIFTY") == 30
    assert resolve_lot_size(SAMPLE_INSTRUMENTS, "SENSEX") == 20


def test_resolve_lot_size_is_case_insensitive():
    assert resolve_lot_size(SAMPLE_INSTRUMENTS, "nifty") == 65


def test_resolve_lot_size_ignores_futures_and_stock_options():
    # if FUTIDX's 999 leaked in, NIFTY would show a conflict and raise
    assert resolve_lot_size(SAMPLE_INSTRUMENTS, "NIFTY") == 65


def test_resolve_lot_size_raises_for_unknown_underlying():
    with pytest.raises(ValueError):
        resolve_lot_size(SAMPLE_INSTRUMENTS, "NOTANINDEX")


def test_resolve_lot_size_raises_when_nothing_found():
    with pytest.raises(LookupError):
        resolve_lot_size([], "NIFTY")


def test_resolve_lot_size_raises_on_conflicting_values():
    conflicting = SAMPLE_INSTRUMENTS + [{"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "lotsize": "75"}]
    with pytest.raises(LookupError):
        resolve_lot_size(conflicting, "NIFTY")


def test_resolve_lot_size_skips_malformed_lotsize_values():
    rows = [
        {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "lotsize": "not-a-number"},
        {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "lotsize": "65"},
    ]
    assert resolve_lot_size(rows, "NIFTY") == 65
