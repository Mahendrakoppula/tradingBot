from unittest.mock import MagicMock, patch

from app.main import check_database


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
