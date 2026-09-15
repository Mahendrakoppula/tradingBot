import datetime as dt

from backtesting.trade_record import Trade
from dashboard.paper_trading_queries import load_all_paper_trading_overviews, load_paper_trading_overview
from paper_trading.state import PaperTradingState, save_state


def _closed_trade(strategy: str, pnl: float) -> Trade:
    return Trade(
        strategy_name=strategy, direction="CE", entry_index=0,
        entry_timestamp=dt.datetime(2026, 1, 1), entry_spot=100.0, entry_premium=5.0,
        strike=100.0, expiry=dt.date(2026, 1, 8), stop_price=95.0, target_price=110.0,
        exit_index=2, exit_timestamp=dt.datetime(2026, 1, 3), exit_spot=105.0, exit_premium=10.0,
        exit_reason="target", pnl=pnl,
    )


def test_overview_for_instrument_with_no_state_yet_is_empty(tmp_path):
    overview = load_paper_trading_overview("NIFTY", state_dir=tmp_path)
    assert overview.open_trade is None
    assert overview.completed_trades == []
    assert overview.summary.n_trades == 0
    assert overview.by_strategy == {}


def test_overview_reflects_persisted_open_trade(tmp_path):
    trade = Trade(
        strategy_name="trend_following", direction="PE", entry_index=10,
        entry_timestamp=dt.datetime(2026, 1, 5), entry_spot=200.0, entry_premium=8.0,
        strike=200.0, expiry=dt.date(2026, 1, 12), stop_price=210.0, target_price=190.0,
    )
    save_state("NIFTY", PaperTradingState(open_trade=trade, last_processed_timestamp="2026-01-05T00:00:00"), state_dir=tmp_path)

    overview = load_paper_trading_overview("NIFTY", state_dir=tmp_path)
    assert overview.open_trade is not None
    assert overview.open_trade.strategy_name == "trend_following"
    assert overview.last_processed_timestamp == "2026-01-05T00:00:00"


def test_overview_summarizes_and_attributes_completed_trades(tmp_path):
    trades = [_closed_trade("trend_following", 10.0), _closed_trade("mean_reversion", -3.0), _closed_trade("trend_following", 5.0)]
    save_state("NIFTY", PaperTradingState(completed_trades=trades), state_dir=tmp_path)

    overview = load_paper_trading_overview("NIFTY", state_dir=tmp_path)
    assert overview.summary.n_trades == 3
    assert overview.summary.total_pnl == 12.0
    assert set(overview.by_strategy.keys()) == {"trend_following", "mean_reversion"}
    assert overview.by_strategy["trend_following"].total_pnl == 15.0
    assert overview.by_strategy["mean_reversion"].total_pnl == -3.0


def test_load_all_overviews_covers_every_instrument(tmp_path):
    overviews = load_all_paper_trading_overviews(state_dir=tmp_path)
    assert {o.instrument for o in overviews} == {"NIFTY", "BANKNIFTY", "SENSEX"}
