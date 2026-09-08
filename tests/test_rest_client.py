from unittest.mock import MagicMock, patch

import pytest

from trading_bot.rest_client import ApiError, RestClient


class FakeConfig:
    root_url = "https://example.test"


class FakeSession:
    def __init__(self):
        self.cfg = FakeConfig()
        self.login_calls = 0

    def headers(self, authenticated=True):
        return {}

    def login(self):
        self.login_calls += 1


def _resp(status: bool, data=None, errorcode: str = "", message: str = ""):
    m = MagicMock()
    m.json.return_value = {"status": status, "data": data, "errorcode": errorcode, "message": message}
    return m


def test_call_succeeds_without_retry():
    session = FakeSession()
    rest = RestClient(session)
    with patch("trading_bot.rest_client.requests.request", return_value=_resp(True, data={"ok": 1})) as m:
        result = rest._call("GET", "/some/path")
    assert result == {"ok": 1}
    assert session.login_calls == 0
    assert m.call_count == 1


def test_session_error_triggers_relogin_and_retry_succeeds():
    session = FakeSession()
    rest = RestClient(session)
    responses = [
        _resp(False, errorcode="AB1011", message="Client not login"),
        _resp(True, data={"ok": 1}),
    ]
    with patch("trading_bot.rest_client.requests.request", side_effect=responses) as m:
        result = rest._call("GET", "/some/path")
    assert result == {"ok": 1}
    assert session.login_calls == 1
    assert m.call_count == 2


def test_non_session_error_does_not_retry():
    session = FakeSession()
    rest = RestClient(session)
    with patch("trading_bot.rest_client.requests.request", return_value=_resp(False, errorcode="AB1009", message="Symbol Not Found")) as m:
        with pytest.raises(ApiError) as exc_info:
            rest._call("GET", "/some/path")
    assert exc_info.value.errorcode == "AB1009"
    assert session.login_calls == 0
    assert m.call_count == 1


def test_session_error_persists_after_retry_still_raises():
    session = FakeSession()
    rest = RestClient(session)
    with patch("trading_bot.rest_client.requests.request", return_value=_resp(False, errorcode="AB1010", message="AMX Session Expired")):
        with pytest.raises(ApiError) as exc_info:
            rest._call("GET", "/some/path")
    assert exc_info.value.errorcode == "AB1010"
    assert session.login_calls == 1  # retried exactly once, not looped forever
