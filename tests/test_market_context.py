from unittest.mock import MagicMock

from features.market_context import OI_BUILDUP_DATATYPES, get_india_vix, get_oi_buildup, get_pcr


def test_get_india_vix_returns_float_on_success():
    client = MagicMock()
    client.get_ltp.return_value = {"ltp": "13.45"}
    result = get_india_vix(client)
    assert result == 13.45
    client.get_ltp.assert_called_once_with("NSE", "India VIX", "99926017")


def test_get_india_vix_returns_none_on_failure():
    client = MagicMock()
    client.get_ltp.side_effect = Exception("network error")
    assert get_india_vix(client) is None


def test_get_pcr_returns_data_on_success():
    client = MagicMock()
    client.get_pcr.return_value = [{"symbol": "NIFTY", "pcr": 0.95}]
    assert get_pcr(client) == [{"symbol": "NIFTY", "pcr": 0.95}]


def test_get_pcr_returns_none_on_failure():
    client = MagicMock()
    client.get_pcr.side_effect = Exception("boom")
    assert get_pcr(client) is None


def test_get_oi_buildup_calls_every_datatype(monkeypatch):
    monkeypatch.setattr("features.market_context.time.sleep", lambda *_: None)
    client = MagicMock()
    client.get_oi_buildup.side_effect = lambda expirytype, datatype: [{"datatype": datatype}]

    result = get_oi_buildup(client)

    assert set(result.keys()) == set(OI_BUILDUP_DATATYPES)
    assert client.get_oi_buildup.call_count == len(OI_BUILDUP_DATATYPES)
    for datatype in OI_BUILDUP_DATATYPES:
        assert result[datatype] == [{"datatype": datatype}]


def test_get_oi_buildup_continues_past_one_failing_datatype(monkeypatch):
    monkeypatch.setattr("features.market_context.time.sleep", lambda *_: None)
    client = MagicMock()

    def side_effect(expirytype, datatype):
        if datatype == "Short Covering":
            raise Exception("failed")
        return [{"datatype": datatype}]

    client.get_oi_buildup.side_effect = side_effect
    result = get_oi_buildup(client)

    assert "Short Covering" not in result
    assert len(result) == len(OI_BUILDUP_DATATYPES) - 1
