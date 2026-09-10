from unittest.mock import MagicMock, patch

from monitoring.notifier import notify


def test_notify_skips_silently_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CODEX_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("CODEX_TELEGRAM_CHAT_ID", raising=False)
    with patch("monitoring.notifier.requests.post") as mock_post:
        notify("hello")
    mock_post.assert_not_called()


def test_notify_posts_to_telegram_when_configured(monkeypatch):
    monkeypatch.setenv("CODEX_TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("CODEX_TELEGRAM_CHAT_ID", "12345")
    mock_resp = MagicMock()
    with patch("monitoring.notifier.requests.post", return_value=mock_resp) as mock_post:
        notify("hello world", html=True)
    mock_post.assert_called_once()
    url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
    assert "test-token" in url
    assert kwargs["json"]["chat_id"] == "12345"
    assert kwargs["json"]["text"] == "hello world"
    assert kwargs["json"]["parse_mode"] == "HTML"
    mock_resp.raise_for_status.assert_called_once()


def test_notify_never_raises_on_request_failure(monkeypatch):
    monkeypatch.setenv("CODEX_TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("CODEX_TELEGRAM_CHAT_ID", "12345")
    with patch("monitoring.notifier.requests.post", side_effect=Exception("network error")):
        notify("hello")  # must not raise
