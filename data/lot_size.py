"""Real, live-verified lot-size lookup for index options - reads the
ACTUAL scrip master's own `lotsize` field per contract, never a
hardcoded number. Exchange-mandated lot sizes are periodically revised
(SEBI's 2024-2025 F&O contract-value revisions materially changed these
more than once) - a hardcoded constant would silently go stale exactly
the way `main`'s own breadth.py warns its NIFTY50_CONSTITUENTS list
could ("won't crash, but will quietly undercount if it drifts").

VERIFIED LIVE (2026-09-15) against the real scrip master:
  - NIFTY: 65
  - BANKNIFTY: 30
  - SENSEX: 20

Same permanent limitation as data/expiry_calendar.py: this is a LIVE
snapshot only, no historical archive - lot sizes have changed multiple
times within the backtester's own 2021-2026 window (same class of issue
expiry_calendar.py's docstring documents for expiry weekday), so this
is usable as "today's real lot size" for an illustrative analysis, not
as a historically-precise reconstruction of what lot size applied on
any given past date.
"""
from data.expiry_calendar import EXPIRY_EXCHANGE_BY_UNDERLYING


def resolve_lot_size(instruments: list[dict], underlying: str) -> int:
    """Lot size of the underlying's currently-listed option contracts.
    Raises LookupError if none found, or if listed contracts disagree on
    lot size (e.g. the scrip master caught mid-transition to a new lot
    size) - never silently picks one of several conflicting values."""
    exch_seg = EXPIRY_EXCHANGE_BY_UNDERLYING.get(underlying.upper())
    if exch_seg is None:
        raise ValueError(f"No known option exchange for underlying {underlying!r} - expected one of {list(EXPIRY_EXCHANGE_BY_UNDERLYING)}")

    lot_sizes = set()
    for row in instruments:
        if (
            str(row.get("name", "")).upper() == underlying.upper()
            and row.get("exch_seg") == exch_seg
            and row.get("instrumenttype") == "OPTIDX"
        ):
            try:
                lot_sizes.add(int(row["lotsize"]))
            except (KeyError, ValueError, TypeError):
                continue

    if not lot_sizes:
        raise LookupError(f"No listed option contracts found for {underlying} to read lot size from - scrip master cache may be stale")
    if len(lot_sizes) > 1:
        raise LookupError(f"Conflicting lot sizes found for {underlying}: {sorted(lot_sizes)} - scrip master may be mid-transition to a new lot size")
    return lot_sizes.pop()
