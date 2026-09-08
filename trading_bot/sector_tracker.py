import logging

from trading_bot.options import find_spot_instrument
from trading_bot.rest_client import RestClient

log = logging.getLogger(__name__)

# NSE sector indices tracked - verified live against the actual scrip master
# (2026-09-09): each one is a plain index row (instrumenttype AMXIDX, exch_seg
# NSE), resolved the exact same way NIFTY/BANKNIFTY spot already is
# (options.find_spot_instrument matches on the scrip master's `name` field).
# Nifty Bank itself isn't listed here - BANKNIFTY's own OI-buildup/momentum
# signal already covers it; these are the sub-sectors used to CONFIRM it.
SECTOR_INDEX_NAMES = {
    "IT": "NIFTY IT",
    "Auto": "NIFTY AUTO",
    "FMCG": "NIFTY FMCG",
    "Pharma": "NIFTY PHARMA",
    "Metal": "NIFTY METAL",
    "Energy": "NIFTY ENERGY",
    "Realty": "NIFTY REALTY",
    "PSU Bank": "NIFTY PSU BANK",
    "Pvt Bank": "NIFTY PVT BANK",
    "Media": "NIFTY MEDIA",
    "Fin Service": "FINNIFTY",
}

# Sub-sectors averaged together as a simple proxy for "is banking broadly
# moving with this trade" - used to confirm a BANKNIFTY direction signal.
BANKNIFTY_CONFIRM_SECTORS = ("PSU Bank", "Pvt Bank")

QUOTE_BATCH_SIZE = 50  # API limit per exchange per request - well above our ~11 sectors, kept for headroom


def resolve_sector_rows(instruments: list[dict]) -> dict[str, dict]:
    """label -> scrip-master row, skipping (with a warning, not a crash) any
    sector index not found - same tolerance as breadth.resolve_constituents,
    in case Angel renames/delists one."""
    rows = {}
    for label, name in SECTOR_INDEX_NAMES.items():
        try:
            rows[label] = find_spot_instrument(instruments, name)
        except LookupError:
            log.warning("Sector index %s (%s) not found in scrip master - skipping", label, name)
    return rows


def _breadth_verdict(advancing: int, declining: int, total: int) -> str:
    """First-cut, unbacktested heuristic: a market-wide confirmation signal
    for NIFTY needs a clear majority (>=60%) of tracked sectors moving the
    same way, not just more up than down."""
    if total == 0:
        return "NEUTRAL"
    if advancing >= total * 0.6:
        return "BULLISH"
    if declining >= total * 0.6:
        return "BEARISH"
    return "NEUTRAL"


def _confirm_verdict(pct_by_label: dict[str, float], labels: tuple, threshold_pct: float) -> str:
    values = [pct_by_label[l] for l in labels if l in pct_by_label]
    if not values:
        return "NEUTRAL"
    avg = sum(values) / len(values)
    if avg >= threshold_pct:
        return "BULLISH"
    if avg <= -threshold_pct:
        return "BEARISH"
    return "NEUTRAL"


def get_sector_snapshot(rest: RestClient, sector_rows: dict[str, dict], move_threshold_pct: float) -> dict:
    """One batch FULL-mode quote call for every tracked sector index (well
    under the 50-symbol/request limit), returning per-sector % change plus a
    directional verdict per strategy underlying (see `allows_direction`).

    Returns {"sectors": [{"label", "pct_change"}, ...] sorted best->worst,
    "advancing", "declining", "underlying_verdict": {"NIFTY": ..., "BANKNIFTY": ...}}.
    """
    tokens = [row["token"] for row in sector_rows.values()]
    token_to_label = {row["token"]: label for label, row in sector_rows.items()}
    data = rest.get_quote("FULL", {"NSE": tokens})
    fetched = data.get("fetched", [])

    sectors = []
    pct_by_label = {}
    for q in fetched:
        label = token_to_label.get(q.get("symbolToken"))
        if label is None:
            continue
        pct = float(q.get("percentChange", 0) or 0)
        pct_by_label[label] = pct
        sectors.append({"label": label, "pct_change": pct})
    sectors.sort(key=lambda s: s["pct_change"], reverse=True)

    advancing = sum(1 for s in sectors if s["pct_change"] >= move_threshold_pct)
    declining = sum(1 for s in sectors if s["pct_change"] <= -move_threshold_pct)

    return {
        "sectors": sectors,
        "advancing": advancing,
        "declining": declining,
        "underlying_verdict": {
            "NIFTY": _breadth_verdict(advancing, declining, len(sectors)),
            "BANKNIFTY": _confirm_verdict(pct_by_label, BANKNIFTY_CONFIRM_SECTORS, move_threshold_pct),
        },
    }


def allows_direction(underlying: str, option_type: str, snapshot: dict) -> bool:
    """Sector-confirmation gate: does today's sector move support this trade
    direction for this underlying? BULLISH requires CE, BEARISH requires PE,
    NEUTRAL (or any underlying with no computed verdict, e.g. a future stock
    add without a sector mapping) imposes no restriction."""
    verdict = snapshot.get("underlying_verdict", {}).get(underlying, "NEUTRAL")
    if verdict == "BULLISH":
        return option_type == "CE"
    if verdict == "BEARISH":
        return option_type == "PE"
    return True


def format_sector_snapshot(snapshot: dict) -> str:
    verdicts = snapshot.get("underlying_verdict", {})
    lines = [f"\U0001F4CA <b>SECTOR SNAPSHOT</b> ({snapshot['advancing']} up / {snapshot['declining']} down)"]
    for s in snapshot["sectors"]:
        dot = "\U0001F7E2" if s["pct_change"] >= 0 else "\U0001F534"
        lines.append(f"{dot} {s['label']} {s['pct_change']:+.2f}%")
    lines.append(f"Gate -> NIFTY: {verdicts.get('NIFTY', 'NEUTRAL')} | BANKNIFTY: {verdicts.get('BANKNIFTY', 'NEUTRAL')}")
    return "\n".join(lines)
