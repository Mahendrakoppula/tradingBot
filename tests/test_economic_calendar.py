from unittest.mock import MagicMock, patch

from features.economic_calendar import get_economic_calendar


def test_returns_none_and_does_not_call_the_api_when_key_is_empty():
    with patch("features.economic_calendar.requests.get") as mock_get:
        result = get_economic_calendar("")
    assert result is None
    mock_get.assert_not_called()


def test_returns_calendar_events_when_key_is_set():
    resp = MagicMock()
    resp.json.return_value = {"economicCalendar": [{"event": "RBI Policy", "country": "IN"}]}
    resp.raise_for_status = MagicMock()
    with patch("features.economic_calendar.requests.get", return_value=resp) as mock_get:
        result = get_economic_calendar("real-key")
    assert result == [{"event": "RBI Policy", "country": "IN"}]
    mock_get.assert_called_once()
    assert mock_get.call_args.kwargs["params"] == {"token": "real-key"}


def test_returns_none_on_request_failure_not_an_exception():
    with patch("features.economic_calendar.requests.get", side_effect=Exception("network error")):
        assert get_economic_calendar("real-key") is None


def test_returns_empty_list_when_response_has_no_events_key():
    resp = MagicMock()
    resp.json.return_value = {}
    resp.raise_for_status = MagicMock()
    with patch("features.economic_calendar.requests.get", return_value=resp):
        assert get_economic_calendar("real-key") == []
