"""Centralized transaction-cost engine for NSE/BSE F&O options - spec's
own requirement: "centralized, never scattered hardcoded assumptions."
Adapted directly from `main`'s trading_bot/costs.py (real formula
structure: brokerage, STT, exchange transaction charges, SEBI turnover
fee, stamp duty, GST) - every codex position is a BOUGHT option (never
sold/written, matching the spec's directional-only scope so far), so STT
(options: sell-side only) and stamp duty (buy-side only) each apply to
exactly one leg; exchange transaction charges and SEBI's fee apply to
both legs; GST applies to (brokerage + exchange charges + SEBI fee)
only, never to STT/stamp duty (themselves taxes, not fee-like charges).

HONESTY CAVEAT (same convention `main` already established for this
exact model): the default rates below are first-cut, publicly-known-
structure defaults, NOT verified against any specific real brokerage
plan or today's exact statutory rates - both change periodically
(broker plan changes, budget announcements, exchange circulars). Every
rate is a TransactionCostRates field specifically so it can be
corrected without a code change.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TransactionCostRates:
    brokerage_per_order: float = 20.0  # flat per executed F&O order - standard discount-broker rate
    stt_sell_pct: float = 0.1  # options STT, SELL side only (we only ever buy, so this hits every exit)
    exchange_txn_pct: float = 0.035  # NSE/BSE F&O transaction charges, both legs
    sebi_fee_pct: float = 0.0001  # SEBI turnover fee (~Rs.10/crore), both legs
    stamp_duty_pct: float = 0.003  # F&O stamp duty, BUY side only
    gst_pct: float = 18.0  # on (brokerage + exchange txn + SEBI fee) only, not on STT/stamp duty


def option_round_trip_cost(entry_premium: float, exit_premium: float, quantity: int, rates: TransactionCostRates = TransactionCostRates()) -> float:
    """Total real-world cost of one BUY-to-open, SELL-to-close options
    round trip, in rupees. `quantity` is the actual number of units
    (lots * lot_size) - the caller decides sizing, this only prices it."""
    entry_turnover = entry_premium * quantity
    exit_turnover = exit_premium * quantity
    brokerage = rates.brokerage_per_order * 2  # one order to enter, one to exit
    stt = exit_turnover * rates.stt_sell_pct / 100
    exchange_txn = (entry_turnover + exit_turnover) * rates.exchange_txn_pct / 100
    sebi_fee = (entry_turnover + exit_turnover) * rates.sebi_fee_pct / 100
    stamp_duty = entry_turnover * rates.stamp_duty_pct / 100
    gst = (brokerage + exchange_txn + sebi_fee) * rates.gst_pct / 100
    return brokerage + stt + exchange_txn + sebi_fee + stamp_duty + gst
