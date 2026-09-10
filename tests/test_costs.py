import pytest

from trading_bot.costs import equity_round_trip_cost, option_round_trip_cost


def test_option_round_trip_cost_basic_breakdown():
    # entry premium 20, exit premium 25, qty 65 (1 NIFTY lot)
    cost = option_round_trip_cost(
        entry_premium=20.0, exit_premium=25.0, quantity=65,
        brokerage_per_order=20.0, stt_sell_pct=0.1, exchange_txn_pct=0.035,
        sebi_fee_pct=0.0001, stamp_duty_pct=0.003, gst_pct=18.0,
    )
    entry_turnover = 20.0 * 65  # 1300
    exit_turnover = 25.0 * 65  # 1625
    brokerage = 20.0 * 2  # 40
    stt = exit_turnover * 0.1 / 100  # 1.625
    exchange_txn = (entry_turnover + exit_turnover) * 0.035 / 100
    sebi_fee = (entry_turnover + exit_turnover) * 0.0001 / 100
    stamp_duty = entry_turnover * 0.003 / 100
    gst = (brokerage + exchange_txn + sebi_fee) * 18.0 / 100
    expected = brokerage + stt + exchange_txn + sebi_fee + stamp_duty + gst
    assert cost == pytest.approx(expected)
    assert cost > brokerage  # sanity: total cost is more than just the flat brokerage


def test_option_round_trip_cost_zero_rates_means_zero_cost():
    cost = option_round_trip_cost(20.0, 25.0, 65, 0, 0, 0, 0, 0, 0)
    assert cost == 0.0


def test_option_round_trip_cost_stamp_duty_only_on_buy_side():
    # exit premium doesn't affect stamp duty - only entry (buy) turnover does
    cost_a = option_round_trip_cost(20.0, 100.0, 65, 0, 0, 0, 0, 0.003, 0)
    cost_b = option_round_trip_cost(20.0, 25.0, 65, 0, 0, 0, 0, 0.003, 0)
    assert cost_a == pytest.approx(cost_b)


def test_equity_round_trip_cost_stt_applies_both_sides():
    # unlike options, equity STT applies to both entry and exit turnover
    cost_zero_exit_stt_only = equity_round_trip_cost(100.0, 100.0, 10, 0, 0.1, 0, 0, 0, 0)
    # entry_turnover=1000, exit_turnover=1000 -> stt = 2000*0.1/100 = 2.0
    assert cost_zero_exit_stt_only == pytest.approx(2.0)


def test_equity_round_trip_cost_free_brokerage_default():
    cost = equity_round_trip_cost(100.0, 110.0, 10, 0.0, 0.1, 0.00297, 0.0001, 0.015, 18.0)
    assert cost > 0  # other charges still apply even with zero brokerage
