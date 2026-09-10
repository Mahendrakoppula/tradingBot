"""Real round-trip transaction cost estimates for NSE/BSE F&O options and
NSE equity delivery trades - brokerage, STT, exchange transaction charges,
SEBI turnover fee, stamp duty, GST - applied to run_technical.py's realized
P&L so paper-mode numbers reflect what a real trade would actually cost,
not just the raw premium/price move. Motivated live 2026-09-10: several
scalp trades closed with a gross P&L of only Rs.20-50/lot, which real
round-trip costs could plausibly wipe out or reverse entirely - the whole
point of costing this is to stop counting a trade as a "win" if a real
account wouldn't have been.

HONESTY CAVEAT (same convention as every other unvalidated threshold in
this project): the RATES below are first-cut, publicly-known-structure
defaults, NOT verified against the user's actual Angel One brokerage plan
or today's statutory rates - both change periodically (broker plan
changes, budget announcements, exchange circulars). Every rate is a
TechnicalConfig field specifically so it can be corrected without a code
change - verify against your real contract note before trusting these
numbers for anything beyond a rough paper-mode approximation.
"""


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
    options'. Brokerage defaults to 0 in TechnicalConfig - equity delivery
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
