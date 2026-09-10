from unittest.mock import MagicMock, patch

import pytest

from data.broker_client import ApiError, BrokerConfig, HistoricalDataClient, Session


def _cfg() -> BrokerConfig:
    return BrokerConfig(
        api_key="key", client_code="client", pin="1234", totp_secret="JBSWY3DPEHPK3PXP",
        local_ip="127.0.0.1", public_ip="127.0.0.1", mac_address="00-00-00-00-00-00",
    )


def test_login_stores_tokens_on_success():
    session = Session(_cfg())
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": True,
        "data": {"jwtToken": "jwt", "refreshToken": "refresh", "feedToken": "feed"},
    }
    with patch("data.broker_client.requests.post", return_value=mock_resp):
        session.login()
    assert session.jwt_token == "jwt"
    assert session.refresh_token == "refresh"
    assert session.feed_token == "feed"


def test_login_raises_auth_error_on_failure():
    from data.broker_client import AuthError
    session = Session(_cfg())
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": False, "message": "bad totp", "errorcode": "AB1050"}
    with patch("data.broker_client.requests.post", return_value=mock_resp):
        with pytest.raises(AuthError):
            session.login()


def test_get_candle_data_returns_data_on_success():
    session = Session(_cfg())
    session.jwt_token = "jwt"
    client = HistoricalDataClient(session)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": True, "data": [["2026-01-01T09:15:00+05:30", 100, 101, 99, 100.5, 1000]]}
    with patch("data.broker_client.requests.post", return_value=mock_resp):
        data = client.get_candle_data("NSE", "99926000", "ONE_DAY", "2026-01-01 09:00", "2026-01-01 15:30")
    assert data == [["2026-01-01T09:15:00+05:30", 100, 101, 99, 100.5, 1000]]


def test_get_candle_data_relogs_in_once_on_session_error():
    session = Session(_cfg())
    session.jwt_token = "stale"
    client = HistoricalDataClient(session)

    session_error_resp = MagicMock()
    session_error_resp.json.return_value = {"status": False, "message": "session expired", "errorcode": "AB1010"}
    login_resp = MagicMock()
    login_resp.json.return_value = {"status": True, "data": {"jwtToken": "fresh", "refreshToken": "r", "feedToken": "f"}}
    success_resp = MagicMock()
    success_resp.json.return_value = {"status": True, "data": [["2026-01-01T09:15:00+05:30", 1, 2, 0.5, 1.5, 10]]}

    with patch("data.broker_client.requests.post", side_effect=[session_error_resp, login_resp, success_resp]) as mock_post:
        data = client.get_candle_data("NSE", "99926000", "ONE_DAY", "2026-01-01 09:00", "2026-01-01 15:30")

    assert data == [["2026-01-01T09:15:00+05:30", 1, 2, 0.5, 1.5, 10]]
    assert session.jwt_token == "fresh"
    assert mock_post.call_count == 3


def test_get_candle_data_raises_non_session_api_errors():
    session = Session(_cfg())
    session.jwt_token = "jwt"
    client = HistoricalDataClient(session)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": False, "message": "bad request", "errorcode": "AB2000"}
    with patch("data.broker_client.requests.post", return_value=mock_resp):
        with pytest.raises(ApiError):
            client.get_candle_data("NSE", "99926000", "ONE_DAY", "2026-01-01 09:00", "2026-01-01 15:30")
