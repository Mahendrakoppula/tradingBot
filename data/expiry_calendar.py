"""Real, live-verified expiry calendar for index options - resolves the
nearest listed expiry for an underlying from the ACTUAL scrip master,
not a guessed/fixed day-of-week or day-count offset.

VERIFIED LIVE (2026-09-15) against the real scrip master:
  - NIFTY (NFO): weekly, Tuesday expiry (NSE moved this from Thursday to
    Tuesday in a 2025 rule change - confirmed live: 2026-09-15 and
    2026-09-22 both land on Tuesday, not Thursday).
  - SENSEX (BFO): weekly, Thursday expiry.
  - BANKNIFTY (NFO): MONTHLY only (NSE discontinued BANKNIFTY weeklies) -
    expiry lands on the last Monday or Tuesday of the month depending on
    holidays (e.g. 2026-11-23 is a Monday, not the 24th - presumably
    because the 24th is a market holiday; the exchange's own
    holiday-aware scheduling is inherited for free by reading its real
    listed contracts, rather than this project trying to compute a
    weekday itself).

IMPORTANT, PERMANENT LIMITATION: this can only resolve expiries for
CURRENTLY LISTED contracts (a live snapshot, same shape/cache pattern
as data/instrument_lookup.py) - it has NO historical archive of past
expiry listings. This makes it genuinely useful for LIVE/paper trading
(paper_trading/), where "today" is always the live date, but NOT usable
for the historical backtester (backtesting/event_loop.py), which runs
over years of past dates the live scrip master has no record of.

Deliberately NOT wired into backtesting/event_loop.py's process_bar()
for exactly this reason: doing so would make process_bar()'s behavior
depend on whether "as of" is live-today or a historical backtest date -
the same live/backtest decision divergence that function's own refactor
(see git history) was built to rule out structurally. The backtester
keeps its existing fixed-days-to-expiry approximation. The historical
caveat is now confirmed concretely, not just theoretical: NIFTY's real
expiry weekday itself changed (Thursday -> Tuesday) at a real point in
history that falls WITHIN the backtester's own multi-year window, so no
single fixed weekday assumption is correct across that whole period -
a genuine, disclosed limitation this module cannot fix for the
backtester. Wiring this into live paper trading is real future work of
its own (process_bar() would need a pluggable expiry resolver instead
of a fixed days_to_expiry int) - not done here, to avoid rushing a
change into an already-tested, shared decision path.
"""
import datetime as dt

EXPIRY_EXCHANGE_BY_UNDERLYING = {
    "NIFTY": "NFO",
    "BANKNIFTY": "NFO",
    "SENSEX": "BFO",
}


def _parse_expiry(expiry_str: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(expiry_str, "%d%b%Y").date()
    except ValueError:
        return None


def list_option_expiries(instruments: list[dict], underlying: str) -> list[dt.date]:
    """Every distinct expiry date currently listed for `underlying`'s
    index options, ascending. Raises ValueError for an underlying this
    module doesn't know the option exchange for - never silently
    returns an empty list for a typo'd name."""
    exch_seg = EXPIRY_EXCHANGE_BY_UNDERLYING.get(underlying.upper())
    if exch_seg is None:
        raise ValueError(f"No known option exchange for underlying {underlying!r} - expected one of {list(EXPIRY_EXCHANGE_BY_UNDERLYING)}")

    dates = set()
    for row in instruments:
        if (
            str(row.get("name", "")).upper() == underlying.upper()
            and row.get("exch_seg") == exch_seg
            and row.get("instrumenttype") == "OPTIDX"
        ):
            parsed = _parse_expiry(row.get("expiry", ""))
            if parsed is not None:
                dates.add(parsed)
    return sorted(dates)


def resolve_nearest_expiry(instruments: list[dict], underlying: str, as_of: dt.date, min_days_to_expiry: int = 0) -> dt.date:
    """Nearest listed expiry on or after as_of + min_days_to_expiry.
    Raises LookupError rather than guessing if nothing qualifies (e.g.
    the scrip master cache is stale and every listed contract has
    already expired) - same "raise, don't guess" discipline as
    data/instrument_lookup.py's find_spot_instrument."""
    cutoff = as_of + dt.timedelta(days=min_days_to_expiry)
    for expiry in list_option_expiries(instruments, underlying):
        if expiry >= cutoff:
            return expiry
    raise LookupError(f"No listed expiry for {underlying} on or after {cutoff} - scrip master cache may be stale")
