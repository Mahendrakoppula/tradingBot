from unittest.mock import MagicMock, patch

from app.main import check_database, main


def test_check_database_returns_true_on_successful_connection():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    with patch("app.main.psycopg2.connect", return_value=mock_conn) as mock_connect:
        result = check_database("postgresql://codex:codex@localhost:5432/codex")
    assert result is True
    mock_connect.assert_called_once()
    mock_cursor.execute.assert_called_once_with("SELECT 1")
    mock_conn.close.assert_called_once()


def test_check_database_returns_false_and_does_not_raise_on_connection_failure():
    with patch("app.main.psycopg2.connect", side_effect=Exception("connection refused")):
        result = check_database("postgresql://bad:bad@localhost:5432/nope")
    assert result is False


def test_main_skips_database_check_when_disabled(monkeypatch):
    """Phase 1 runs on the shared 1GB instance with no database - the
    startup notification must say so, and check_database must never be
    called (no point failing a health check against a DB that was never
    supposed to exist yet)."""
    monkeypatch.delenv("DATABASE_ENABLED", raising=False)
    monkeypatch.setenv("CODEX_TELEGRAM_BOT_TOKEN", "")

    with (
        patch("app.main.check_database") as mock_check_db,
        patch("app.main.notify") as mock_notify,
        patch("app.main.setup_logging"),
        patch("app.main.time.sleep", side_effect=KeyboardInterrupt),
    ):
        try:
            main()
        except KeyboardInterrupt:
            pass

    mock_check_db.assert_not_called()
    startup_message = mock_notify.call_args_list[0].args[0]
    assert "disabled" in startup_message.lower()
