import logging

from trading_bot import breadth
from trading_bot.market_context import snapshot
from trading_bot.rest_client import RestClient

log = logging.getLogger(__name__)


def build_morning_briefing(rest: RestClient, instruments: list[dict]) -> str:
    """One-shot snapshot of VIX/PCR/global cues + NIFTY50/BANKNIFTY breadth,
    formatted as a plain-text Telegram message. Called once at startup -
    not refreshed through the day yet (a natural next step if wanted)."""
    ctx = snapshot(rest)
    nifty_breadth = breadth.get_breadth(rest, instruments, breadth.NIFTY50_CONSTITUENTS)
    bank_breadth = breadth.get_breadth(rest, instruments, breadth.BANKNIFTY_CONSTITUENTS)

    lines = ["Morning Briefing"]
    lines.append(f"India VIX: {ctx.india_vix:.2f}" if ctx.india_vix is not None else "India VIX: n/a")

    if nifty_breadth:
        b = nifty_breadth
        lines.append(f"NIFTY50 breadth: {b['advancing']} up / {b['declining']} down / {b['unchanged']} flat (of {b['count']})")
        lines.append("  Gainers: " + ", ".join(f"{s} {p:+.2f}%" for s, p in b["top_gainers"]))
        lines.append("  Losers: " + ", ".join(f"{s} {p:+.2f}%" for s, p in b["top_losers"]))
        lines.append("  Volume leaders: " + ", ".join(f"{s} {v:,}" for s, v in b["top_volume"]))
    else:
        lines.append("NIFTY50 breadth: unavailable")

    if bank_breadth:
        b = bank_breadth
        lines.append(f"BANKNIFTY breadth: {b['advancing']} up / {b['declining']} down / {b['unchanged']} flat (of {b['count']})")
    else:
        lines.append("BANKNIFTY breadth: unavailable")

    if ctx.global_quotes:
        gq = ctx.global_quotes
        parts = []
        if "sp500" in gq:
            parts.append(f"S&P500 {gq['sp500']:.0f}")
        if "dow" in gq:
            parts.append(f"Dow {gq['dow']:.0f}")
        if "nasdaq" in gq:
            parts.append(f"Nasdaq {gq['nasdaq']:.0f}")
        if "crude_wti" in gq:
            parts.append(f"Crude {gq['crude_wti']:.1f}")
        if "usd_inr" in gq:
            parts.append(f"USD/INR {gq['usd_inr']:.2f}")
        if parts:
            lines.append("Global cues: " + ", ".join(parts))

    if ctx.economic_calendar is not None:
        lines.append(f"Economic calendar entries today: {len(ctx.economic_calendar)}")

    return "\n".join(lines)
