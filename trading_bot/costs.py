"""Real round-trip transaction cost estimates for NSE/BSE F&O options and
NSE equity delivery trades - brokerage, STT, exchange transaction charges,
SEBI turnover fee, stamp duty, GST - so paper-mode P&L reflects what a
real trade would actually cost, not just the raw premium/price move.
Motivated live 2026-09-10: several scalp trades closed with a gross P&L of
only Rs.20-50/lot, which real round-trip costs could plausibly wipe out or
reverse entirely - the whole point of costing this is to stop counting a
trade as a "win" if a real account wouldn't have been.

HONESTY CAVEAT (same convention as every other unvalidated threshold in
this project): the RATES in CostRates are first-cut, publicly-known-
structure defaults, NOT verified against the user's actual Angel One
brokerage plan or today's statutory rates - both change periodically
(broker plan changes, budget announcements, exchange circulars). Verify
against a real contract note before trusting these numbers for anything
beyond a rough paper-mode approximation.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CostRates:
    """One bundle of rates for both cost functions below. Statutory rates
    are not tuning knobs - tests/test_costs.py pins these defaults."""

    brokerage_per_order: float = 20.0  # flat per executed F&O order - standard discount-broker rate
    stt_sell_pct: float = 0.1  # options STT, SELL side only (we only ever buy, so this hits every exit)
    exchange_txn_pct: float = 0.035  # NSE/BSE F&O transaction charges, both legs
    sebi_fee_pct: float = 0.0001  # SEBI turnover fee (~Rs.10/crore), both legs
    stamp_duty_pct: float = 0.003  # F&O stamp duty, BUY side only
    gst_pct: float = 18.0  # on (brokerage + exchange txn + SEBI fee) only, not on STT/stamp duty
    equity_brokerage_per_order: float = 0.0  # equity DELIVERY (CNC) is commission-free on most discount broker plans
    equity_stt_pct: float = 0.1  # equity delivery STT applies to BOTH legs, unlike options' sell-only
    equity_stamp_duty_pct: float = 0.015  # equity delivery stamp duty is higher than F&O's, BUY side only

    def option_cost(self, entry_premium: float, exit_premium: float, quantity: int) -> float:
        return option_round_trip_cost(
            entry_premium, exit_premium, quantity,
            self.brokerage_per_order, self.stt_sell_pct, self.exchange_txn_pct,
            self.sebi_fee_pct, self.stamp_duty_pct, self.gst_pct,
        )

    def equity_cost(self, entry_price: float, exit_price: float, quantity: int) -> float:
        return equity_round_trip_cost(
            entry_price, exit_price, quantity,
            self.equity_brokerage_per_order, self.equity_stt_pct, self.exchange_txn_pct,
            self.sebi_fee_pct, self.equity_stamp_duty_pct, self.gst_pct,
        )


def option_round_trip_cost(
    entry_premium: float, exit_premium: float, quantity: int,
    brokerage_per_order: float, stt_sell_pct: float, exchange_txn_pct: float,
    sebi_fee_pct: float, stamp_duty_pct: float, gst_pct: float,
) -> float:
    """Every position here is a BOUGHT option (never sold/written), so STT
    (options: sell-side only) and stamp duty (buy-side only) each apply to
    exactly one leg. Exchange transaction charges and SEBI's turnover fee
    apply to both legs. GST applies to (brokerage + exchange charges + SEBI
    fee) only - not to STT or stamp duty, which are themselves taxes."""
    entry_turnover = entry_premium * quantity
    exit_turnover = exit_premium * quantity
    brokerage = brokerage_per_order * 2  # one order to enter, one to exit
    stt = exit_turnover * stt_sell_pct / 100
    exchange_txn = (entry_turnover + exit_turnover) * exchange_txn_pct / 100
    sebi_fee = (entry_turnover + exit_turnover) * sebi_fee_pct / 100
    stamp_duty = entry_turnover * stamp_duty_pct / 100
    gst = (brokerage + exchange_txn + sebi_fee) * gst_pct / 100
    return brokerage + stt + exchange_txn + sebi_fee + stamp_duty + gst


def equity_round_trip_cost(
    entry_price: float, exit_price: float, quantity: int,
    brokerage_per_order: float, stt_pct: float, exchange_txn_pct: float,
    sebi_fee_pct: float, stamp_duty_pct: float, gst_pct: float,
) -> float:
    """NSE equity DELIVERY (CNC), not intraday - STT applies to BOTH legs
    here (unlike options' sell-side-only), at a different rate than
    options'. Brokerage defaults to 0 in CostRates - equity delivery
    is commission-free on most Indian discount broker plans including
    Angel One's, unlike F&O - but kept as a parameter rather than hardcoded
    in case that ever changes or doesn't match your specific plan."""
    entry_turnover = entry_price * quantity
    exit_turnover = exit_price * quantity
    brokerage = brokerage_per_order * 2
    stt = (entry_turnover + exit_turnover) * stt_pct / 100
    exchange_txn = (entry_turnover + exit_turnover) * exchange_txn_pct / 100
    sebi_fee = (entry_turnover + exit_turnover) * sebi_fee_pct / 100
    stamp_duty = entry_turnover * stamp_duty_pct / 100
    gst = (brokerage + exchange_txn + sebi_fee) * gst_pct / 100
    return brokerage + stt + exchange_txn + sebi_fee + stamp_duty + gst
