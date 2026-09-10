import pytest

from config.settings import Settings


def test_settings_loads_with_defaults(monkeypatch):
    monkeypatch.delenv("CAPITAL", raising=False)
    s = Settings(_env_file=None)
    assert s.capital == 50_000.0
    assert s.max_daily_loss == 2_000.0
    assert s.profit_protection_level == 800.0
    assert s.profit_selectivity_level == 1_000.0
    assert s.instruments == ("NIFTY", "BANKNIFTY", "SENSEX")
    assert s.dry_run is True
    assert s.database_enabled is False


def test_settings_reads_environment_overrides(monkeypatch):
    monkeypatch.setenv("CAPITAL", "100000")
    monkeypatch.setenv("MAX_DAILY_LOSS", "3000")
    s = Settings(_env_file=None)
    assert s.capital == 100_000.0
    assert s.max_daily_loss == 3_000.0


def test_capital_must_be_positive():
    with pytest.raises(ValueError):
        Settings(_env_file=None, capital=0)


def test_max_daily_loss_cannot_exceed_capital():
    with pytest.raises(ValueError):
        Settings(_env_file=None, capital=50_000.0, max_daily_loss=60_000.0)


def test_selectivity_level_must_be_at_or_above_protection_level():
    with pytest.raises(ValueError):
        Settings(_env_file=None, profit_protection_level=1000.0, profit_selectivity_level=800.0)


def test_selectivity_level_equal_to_protection_level_is_allowed():
    s = Settings(_env_file=None, profit_protection_level=800.0, profit_selectivity_level=800.0)
    assert s.profit_selectivity_level == 800.0


def test_telegram_and_broker_credentials_default_empty_not_missing():
    """Empty, not a validation error - notifier.py's own best-effort
    behavior is what handles an unconfigured token, not a hard startup
    failure (codex may run its Phase 1 health check before the user has
    provided the bot token)."""
    s = Settings(_env_file=None)
    assert s.codex_telegram_bot_token == ""
    assert s.codex_telegram_chat_id == ""
