import datetime as dt

import pytest

from backtesting.tick_slippage import (
    DEFAULT_TICK_SPREAD_LEVELS,
    REAL_TICK_SIZE_RUPEES,
    tick_adjusted_pnl,
    tick_slippage_cost,
    tick_slippage_sweep,
)
from backtesting.trade_record import Trade
from execution.transaction_costs import TransactionCostRates

ZERO_COST_RATES = TransactionCostRates(brokerage_per_order=0, stt_sell_pct=0, exchange_txn_pct=0, sebi_fee_pct=0, stamp_duty_pct=0, gst_pct=0)


def _trade(entry_premium: float, exit_premium: float) -> Trade:
    return Trade(
        strategy_name="t", direction="CE", entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=entry_premium, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0, exit_index=2, exit_timestamp=dt.datetime(2026, 1, 3),
        exit_spot=100.0, exit_premium=exit_premium, exit_reason="target", pnl=exit_premium - entry_premium,
    )


def test_real_tick_size_is_the_corrected_value_not_the_raw_scrip_master_field():
    """Regression check for the exact data-interpretation catch this
    module's docstring documents: the raw scrip master tick_size field
    reads 5.000000, but that schema scales price-like fields by 100
    (confirmed via the strike field) - the real tick is 0.05, not 5."""
    assert REAL_TICK_SIZE_RUPEES == 0.05


def test_tick_slippage_cost_scales_with_ticks_and_quantity():
    assert tick_slippage_cost(n_ticks_round_trip=1, quantity=65) == pytest.approx(0.05 * 65)
    assert tick_slippage_cost(n_ticks_round_trip=10, quantity=65) == pytest.approx(0.5 * 65)
    assert tick_slippage_cost(n_ticks_round_trip=0, quantity=65) == 0.0


def test_tick_adjusted_pnl_zero_ticks_matches_pure_cost_adjustment():
    from backtesting.cost_adjustment import cost_adjust_trade
    trade = _trade(10.0, 15.0)
    tick_adjusted = tick_adjusted_pnl(trade, lot_size=65, n_ticks_round_trip=0)
    cost_only = cost_adjust_trade(trade, lot_size=65).net_pnl
    assert tick_adjusted == pytest.approx(cost_only)


def test_tick_adjusted_pnl_hits_cheap_premiums_much_harder_percentagewise():
    """The whole point of this model vs. the flat-percentage one: the
    SAME absolute tick cost is a much bigger fraction of a cheap
    far-OTM trade's gross P&L than an expensive ATM one."""
    cheap_trade = _trade(5.0, 6.0)  # gross = 1.0/unit
    expensive_trade = _trade(200.0, 201.0)  # gross = 1.0/unit, same absolute gross
    lot_size = 65

    cheap_net = tick_adjusted_pnl(cheap_trade, lot_size, n_ticks_round_trip=15, rates=ZERO_COST_RATES)
    expensive_net = tick_adjusted_pnl(expensive_trade, lot_size, n_ticks_round_trip=15, rates=ZERO_COST_RATES)

    cheap_gross = 1.0 * lot_size
    expensive_gross = 1.0 * lot_size
    # both lose the SAME absolute tick cost (ticks don't scale with
    # premium), so both nets should be identical here - the point is
    # this SAME absolute cost is proportionally devastating for the
    # cheap trade and trivial for the expensive one
    assert cheap_net == pytest.approx(expensive_net)
    cheap_cost_fraction = (cheap_gross - cheap_net) / cheap_gross
    assert cheap_cost_fraction > 0.5  # ticks alone eat over half the gross P&L on the cheap trade


def test_tick_adjusted_pnl_raises_for_open_trade():
    open_trade = Trade(
        strategy_name="t", direction="CE", entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=10.0, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0,
    )
    with pytest.raises(ValueError):
        tick_adjusted_pnl(open_trade, lot_size=65, n_ticks_round_trip=5)


def test_sweep_produces_one_point_per_level_in_order():
    trades = [_trade(10.0, 15.0), _trade(20.0, 18.0)]
    result = tick_slippage_sweep(trades, lot_size=65)
    assert [p.n_ticks for p in result] == list(DEFAULT_TICK_SPREAD_LEVELS)


def test_sweep_total_pnl_is_monotonically_non_increasing_with_ticks():
    trades = [_trade(10.0, 15.0), _trade(20.0, 18.0), _trade(30.0, 40.0)]
    result = tick_slippage_sweep(trades, lot_size=65)
    pnls = [p.summary.total_pnl for p in result]
    assert pnls == sorted(pnls, reverse=True)


def test_sweep_ignores_open_trades():
    open_trade = Trade(
        strategy_name="t", direction="CE", entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=10.0, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0,
    )
    result = tick_slippage_sweep([_trade(10.0, 15.0), open_trade], lot_size=65)
    assert all(p.summary.n_trades == 1 for p in result)
