import datetime as dt

from backtesting.trade_record import Trade
from paper_trading.state import PaperTradingState, load_state, save_state


def test_load_state_when_nothing_saved_yet_returns_empty_state(tmp_path):
    state = load_state("NIFTY", state_dir=tmp_path)
    assert state.open_trade is None
    assert state.completed_trades == []
    assert state.last_processed_timestamp is None


def test_save_and_load_round_trips_an_open_trade(tmp_path):
    trade = Trade(
        strategy_name="trend_following", direction="CE", entry_index=100,
        entry_timestamp=dt.datetime(2026, 1, 5, 0, 0), entry_spot=24500.0, entry_premium=120.5,
        strike=24500.0, expiry=dt.date(2026, 1, 12), stop_price=24300.0, target_price=24900.0,
        entry_regime="TRENDING_UP",
    )
    save_state("NIFTY", PaperTradingState(open_trade=trade, last_processed_timestamp="2026-01-05T00:00:00"), state_dir=tmp_path)

    loaded = load_state("NIFTY", state_dir=tmp_path)
    assert loaded.open_trade is not None
    assert loaded.open_trade.strategy_name == "trend_following"
    assert loaded.open_trade.entry_timestamp == dt.datetime(2026, 1, 5, 0, 0)
    assert loaded.open_trade.expiry == dt.date(2026, 1, 12)
    assert loaded.last_processed_timestamp == "2026-01-05T00:00:00"


def test_save_and_load_round_trips_completed_trades(tmp_path):
    closed = Trade(
        strategy_name="mean_reversion", direction="PE", entry_index=50,
        entry_timestamp=dt.datetime(2026, 1, 1), entry_spot=24000.0, entry_premium=100.0,
        strike=24000.0, expiry=dt.date(2026, 1, 8), stop_price=24200.0, target_price=23600.0,
        exit_index=52, exit_timestamp=dt.datetime(2026, 1, 3), exit_spot=23700.0, exit_premium=150.0,
        exit_reason="target", pnl=50.0,
    )
    save_state("NIFTY", PaperTradingState(completed_trades=[closed]), state_dir=tmp_path)

    loaded = load_state("NIFTY", state_dir=tmp_path)
    assert len(loaded.completed_trades) == 1
    assert loaded.completed_trades[0].pnl == 50.0
    assert loaded.completed_trades[0].exit_reason == "target"


def test_state_is_isolated_per_instrument(tmp_path):
    save_state("NIFTY", PaperTradingState(last_processed_timestamp="A"), state_dir=tmp_path)
    save_state("BANKNIFTY", PaperTradingState(last_processed_timestamp="B"), state_dir=tmp_path)

    assert load_state("NIFTY", state_dir=tmp_path).last_processed_timestamp == "A"
    assert load_state("BANKNIFTY", state_dir=tmp_path).last_processed_timestamp == "B"


def test_save_creates_the_state_directory_if_missing(tmp_path):
    nested = tmp_path / "does" / "not" / "exist"
    save_state("NIFTY", PaperTradingState(), state_dir=nested)
    assert (nested / "NIFTY.json").exists()
