from unittest.mock import MagicMock, patch

from features.global_context import (
    WORLD_BANK_INDICATORS,
    YAHOO_SYMBOLS,
    get_global_change_pct,
    get_global_cues,
    get_global_quote,
    get_macro_indicator,
    get_macro_snapshot,
)


def _yahoo_response(price=100.0, change_pct=1.5):
    resp = MagicMock()
    resp.json.return_value = {"chart": {"result": [{"meta": {"regularMarketPrice": price, "regularMarketChangePercent": change_pct}}]}}
    resp.raise_for_status = MagicMock()
    return resp


def test_get_global_quote_returns_price_on_success():
    with patch("features.global_context.requests.get", return_value=_yahoo_response(price=5500.25)):
        assert get_global_quote("^GSPC") == 5500.25


def test_get_global_quote_returns_none_on_failure():
    with patch("features.global_context.requests.get", side_effect=Exception("network error")):
        assert get_global_quote("^GSPC") is None


def test_get_global_change_pct_returns_percentage():
    with patch("features.global_context.requests.get", return_value=_yahoo_response(change_pct=-2.3)):
        assert get_global_change_pct("^GSPC") == -2.3


def test_get_global_change_pct_returns_none_when_field_missing():
    resp = MagicMock()
    resp.json.return_value = {"chart": {"result": [{"meta": {"regularMarketPrice": 100.0}}]}}
    resp.raise_for_status = MagicMock()
    with patch("features.global_context.requests.get", return_value=resp):
        assert get_global_change_pct("^GSPC") is None


def test_get_global_cues_covers_every_symbol_and_skips_failures():
    def fake_get(url, params, headers, timeout):
        if "GSPC" in url:
            raise Exception("down")
        return _yahoo_response(price=42.0)

    with patch("features.global_context.requests.get", side_effect=fake_get):
        result = get_global_cues()

    assert "sp500" not in result  # the one that failed
    assert set(result.keys()) == set(YAHOO_SYMBOLS.keys()) - {"sp500"}
    assert all(v == 42.0 for v in result.values())


def _world_bank_response(value=5.2):
    resp = MagicMock()
    resp.json.return_value = [{}, [{"value": value}]]
    resp.raise_for_status = MagicMock()
    return resp


def test_get_macro_indicator_returns_value_on_success():
    with patch("features.global_context.requests.get", return_value=_world_bank_response(4.8)):
        assert get_macro_indicator("FP.CPI.TOTL.ZG") == 4.8


def test_get_macro_indicator_returns_none_on_failure():
    with patch("features.global_context.requests.get", side_effect=Exception("boom")):
        assert get_macro_indicator("FP.CPI.TOTL.ZG") is None


def test_get_macro_snapshot_covers_every_indicator():
    with patch("features.global_context.requests.get", return_value=_world_bank_response(3.1)):
        result = get_macro_snapshot()
    assert set(result.keys()) == set(WORLD_BANK_INDICATORS.keys())
    assert all(v == 3.1 for v in result.values())
