from unittest.mock import MagicMock, patch

from data.broker_client import BrokerConfig, Session
from execution.order_client import OrderClient


def _session() -> Session:
    cfg = BrokerConfig(
        api_key="key", client_code="client", pin="1234", totp_secret="JBSWY3DPEHPK3PXP",
        local_ip="127.0.0.1", public_ip="127.0.0.1", mac_address="00-00-00-00-00-00",
    )
    session = Session(cfg)
    session.jwt_token = "jwt"
    return session


def test_place_order_simulates_when_dry_run_true_even_if_environment_live():
    client = OrderClient(_session(), environment="live", dry_run=True)
    with patch("execution.order_client.requests.request") as mock_request:
        result = client.place_order({"tradingsymbol": "NIFTY"})
    assert result["simulated"] is True
    mock_request.assert_not_called()


def test_place_order_simulates_when_environment_is_not_live_even_if_dry_run_false():
    client = OrderClient(_session(), environment="paper", dry_run=False)
    with patch("execution.order_client.requests.request") as mock_request:
        result = client.place_order({"tradingsymbol": "NIFTY"})
    assert result["simulated"] is True
    mock_request.assert_not_called()


def test_place_order_calls_real_broker_only_when_both_flags_agree():
    client = OrderClient(_session(), environment="live", dry_run=False)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": True, "data": {"orderid": "123"}}
    with patch("execution.order_client.requests.request", return_value=mock_resp) as mock_request:
        result = client.place_order({"tradingsymbol": "NIFTY"})
    mock_request.assert_called_once()
    assert result == {"orderid": "123"}


def test_get_order_book_always_calls_real_broker_regardless_of_dry_run():
    client = OrderClient(_session(), environment="dry_run", dry_run=True)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": True, "data": [{"orderid": "1"}]}
    with patch("execution.order_client.requests.request", return_value=mock_resp) as mock_request:
        result = client.get_order_book()
    mock_request.assert_called_once()
    assert result == [{"orderid": "1"}]


def test_get_order_book_returns_empty_list_when_data_is_none():
    client = OrderClient(_session(), environment="live", dry_run=False)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": True, "data": None}
    with patch("execution.order_client.requests.request", return_value=mock_resp):
        result = client.get_order_book()
    assert result == []


def test_live_trading_enabled_requires_both_flags():
    assert OrderClient(_session(), "live", False).live_trading_enabled is True
    assert OrderClient(_session(), "live", True).live_trading_enabled is False
    assert OrderClient(_session(), "paper", False).live_trading_enabled is False
    assert OrderClient(_session(), "dry_run", True).live_trading_enabled is False
