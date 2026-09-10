import json
from unittest.mock import MagicMock, patch

import pytest

from data.instrument_lookup import InstrumentLookup, find_spot_instrument

SAMPLE_INSTRUMENTS = [
    {"name": "NIFTY", "instrumenttype": "AMXIDX", "exch_seg": "NSE", "token": "99926000", "symbol": "NIFTY"},
    {"name": "BANKNIFTY", "instrumenttype": "AMXIDX", "exch_seg": "NSE", "token": "99926009", "symbol": "BANKNIFTY"},
    {"name": "SENSEX", "instrumenttype": "AMXIDX", "exch_seg": "BSE", "token": "99919000", "symbol": "SENSEX"},
    {"name": "RELIANCE", "instrumenttype": "", "exch_seg": "NSE", "token": "2885", "symbol": "RELIANCE-EQ"},
]


def test_find_spot_instrument_resolves_nse_index():
    row = find_spot_instrument(SAMPLE_INSTRUMENTS, "nifty")
    assert row["token"] == "99926000"


def test_find_spot_instrument_resolves_bse_index():
    row = find_spot_instrument(SAMPLE_INSTRUMENTS, "SENSEX")
    assert row["exch_seg"] == "BSE"
    assert row["token"] == "99919000"


def test_find_spot_instrument_raises_lookup_error_for_unknown_underlying():
    with pytest.raises(LookupError):
        find_spot_instrument(SAMPLE_INSTRUMENTS, "NOTANINDEX")


def test_find_spot_instrument_does_not_match_equity_rows():
    """Equity rows (instrumenttype "") must never satisfy an index lookup,
    even if a name happened to collide - Phase 2 is index-only scope."""
    with pytest.raises(LookupError):
        find_spot_instrument(SAMPLE_INSTRUMENTS, "RELIANCE")


def test_load_downloads_and_caches_when_no_cache_exists(tmp_path):
    cache_path = tmp_path / "scrip_master.json"
    lookup = InstrumentLookup("https://example.invalid/scrip.json", cache_path=cache_path)
    mock_resp = MagicMock()
    mock_resp.json.return_value = SAMPLE_INSTRUMENTS
    mock_resp.raise_for_status = MagicMock()
    with patch("data.instrument_lookup.requests.get", return_value=mock_resp) as mock_get:
        lookup.load()
    assert lookup.instruments == SAMPLE_INSTRUMENTS
    assert cache_path.exists()
    assert json.loads(cache_path.read_text()) == SAMPLE_INSTRUMENTS
    mock_get.assert_called_once()


def test_load_uses_cache_without_network_call_when_fresh(tmp_path):
    cache_path = tmp_path / "scrip_master.json"
    cache_path.write_text(json.dumps(SAMPLE_INSTRUMENTS), encoding="utf-8")
    lookup = InstrumentLookup("https://example.invalid/scrip.json", cache_path=cache_path)
    with patch("data.instrument_lookup.requests.get") as mock_get:
        lookup.load()
    assert lookup.instruments == SAMPLE_INSTRUMENTS
    mock_get.assert_not_called()
