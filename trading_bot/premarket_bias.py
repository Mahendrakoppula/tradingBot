import logging

from trading_bot.market_context import get_economic_calendar, get_global_change_pct, get_india_vix
from trading_bot.rest_client import RestClient

log = logging.getLogger(__name__)

# First-cut, UNVALIDATED heuristic - not backtested. Combines genuinely
# LEADING signals, all computed before Indian markets open (unlike the
# OI-buildup/momentum signal in debit_strategy.py, which is inherently
# lagging - it needs today's price/OI to have already moved): the prior
# US session's own close (S&P 500 - it closed hours before India opens, so
# its move is fully known ahead of time), India VIX level, and whether a
# major economic event is scheduled today.


def compute_premarket_bias(rest: RestClient, us_move_threshold_pct: float, vix_caution_level: float) -> dict:
    """Returns {"bias": "BULLISH"|"BEARISH"|"NEUTRAL"|"CAUTIOUS", "reasons":
    [str], "us_overnight_pct": float|None, "vix": float|None,
    "economic_events_today": int|None}. Computed ONCE per day, before the
    entry window - not refreshed intraday."""
    us_change = get_global_change_pct("^GSPC")
    vix = get_india_vix(rest)
    calendar = get_economic_calendar()

    reasons = []
    bias = "NEUTRAL"

    if vix is not None and vix >= vix_caution_level:
        bias = "CAUTIOUS"
        reasons.append(f"India VIX elevated at {vix:.2f} (>= {vix_caution_level})")

    if calendar:
        bias = "CAUTIOUS"
        reasons.append(f"{len(calendar)} economic event(s) scheduled today")

    if bias != "CAUTIOUS":
        if us_change is not None and us_change >= us_move_threshold_pct:
            bias = "BULLISH"
            reasons.append(f"S&P 500 closed up {us_change:+.2f}% overnight")
        elif us_change is not None and us_change <= -us_move_threshold_pct:
            bias = "BEARISH"
            reasons.append(f"S&P 500 closed down {us_change:+.2f}% overnight")
        else:
            reasons.append(
                "no strong overnight lean"
                if us_change is not None
                else "overnight S&P 500 data unavailable"
            )

    return {
        "bias": bias,
        "reasons": reasons,
        "us_overnight_pct": us_change,
        "vix": vix,
        "economic_events_today": len(calendar) if calendar is not None else None,
    }


def allows_direction(bias: dict, option_type: str) -> bool:
    """Does today's pre-market bias permit this trade direction? CAUTIOUS
    blocks everything; BULLISH/BEARISH require the intraday signal to agree;
    NEUTRAL imposes no extra restriction (no strong pre-market lean either
    way, so let the intraday signal decide freely)."""
    b = bias["bias"]
    if b == "CAUTIOUS":
        return False
    if b == "BULLISH":
        return option_type == "CE"
    if b == "BEARISH":
        return option_type == "PE"
    return True  # NEUTRAL


def format_bias_line(bias: dict) -> str:
    parts = [f"Pre-market bias: {bias['bias']}"]
    if bias["reasons"]:
        parts.append(" - " + "; ".join(bias["reasons"]))
    return "".join(parts)
