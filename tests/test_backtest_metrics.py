from backtesting.metrics import summarize
from backtesting.trade_record import Trade


def _closed_trade(pnl: float) -> Trade:
    return Trade("t", "CE", 0, None, 100.0, 5.0, 100.0, None, stop_price=95, target_price=110, pnl=pnl)


def test_empty_trade_list_gives_zeroed_summary():
    summary = summarize([])
    assert summary.n_trades == 0
    assert summary.win_rate is None
    assert summary.avg_pnl_per_trade is None
    assert summary.total_pnl == 0.0
    assert summary.max_drawdown == 0.0


def test_all_winners():
    summary = summarize([_closed_trade(10), _closed_trade(20)])
    assert summary.n_trades == 2
    assert summary.n_wins == 2
    assert summary.n_losses == 0
    assert summary.win_rate == 1.0
    assert summary.total_pnl == 30.0
    assert summary.avg_pnl_per_trade == 15.0
    assert summary.max_drawdown == 0.0  # cumulative pnl never dips below its own running peak


def test_mixed_wins_and_losses_win_rate_and_total():
    summary = summarize([_closed_trade(50), _closed_trade(-20), _closed_trade(30), _closed_trade(-10)])
    assert summary.n_trades == 4
    assert summary.n_wins == 2
    assert summary.n_losses == 2
    assert summary.win_rate == 0.5
    assert summary.total_pnl == 50.0


def test_max_drawdown_tracks_the_worst_peak_to_trough_decline():
    # cumulative pnl path: 100, 60, 90, 40 -> peak 100, trough after peak = 40 -> drawdown 60
    summary = summarize([_closed_trade(100), _closed_trade(-40), _closed_trade(30), _closed_trade(-50)])
    assert summary.max_drawdown == 60.0


def test_a_zero_pnl_trade_counts_as_a_loss_not_a_win():
    summary = summarize([_closed_trade(0.0)])
    assert summary.n_wins == 0
    assert summary.n_losses == 1


def test_open_trades_without_pnl_are_excluded():
    open_trade = Trade("t", "CE", 0, None, 100.0, 5.0, 100.0, None, stop_price=95, target_price=110)
    summary = summarize([open_trade, _closed_trade(10)])
    assert summary.n_trades == 1
