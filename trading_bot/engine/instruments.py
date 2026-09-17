"""Instrument resolution for the engine: each underlying's spot index token
plus its near-month index future as the volume proxy (index ticks carry
volume=0). Resolved once at startup from the scrip master, after which the
master is dropped to bound memory.

Verified tokens (spec plan): NIFTY 99926000/NSE, BANKNIFTY 99926009/NSE,
SENSEX 99919000/BSE - we still resolve from the master and fail loudly if
it disagrees, rather than trusting constants.
"""
import datetime as dt
import logging

from trading_bot.engine.warmup import Instrument
from trading_bot.options import find_spot_instrument

log = logging.getLogger(__name__)

WS_EXCHANGE_TYPE = {"NSE": "nse_cm", "BSE": "bse_cm", "NFO": "nse_fo", "BFO": "bse_fo"}
FUTURES_EXCHANGE = {"NSE": "NFO", "BSE": "BFO"}
EXPECTED_SPOT = {"NIFTY": ("99926000", "NSE"), "BANKNIFTY": ("99926009", "NSE"), "SENSEX": ("99919000", "BSE")}


def _parse_expiry(raw: str) -> dt.date:
    return dt.datetime.strptime(raw, "%d%b%Y").date()


def near_month_future(rows: list[dict], underlying: str, exchange: str, today: dt.date) -> dict | None:
    cands = []
    for r in rows:
        if str(r.get("name", "")).upper() != underlying or r.get("instrumenttype") != "FUTIDX" or r.get("exch_seg") != exchange:
            continue
        try:
            exp = _parse_expiry(r["expiry"])
        except (KeyError, ValueError):
            continue
        if exp >= today:
            cands.append((exp, r))
    if not cands:
        return None
    return min(cands, key=lambda x: x[0])[1]


def resolve_instruments(rows: list[dict], underlyings: tuple[str, ...], today: dt.date,
                        volume_proxy: str = "futures") -> list[Instrument]:
    out: list[Instrument] = []
    for u in underlyings:
        spot = find_spot_instrument(rows, u)
        exp = EXPECTED_SPOT.get(u)
        if exp and (str(spot["token"]), spot["exch_seg"]) != exp:
            raise RuntimeError(f"{u} spot resolved to {spot['token']}/{spot['exch_seg']}, expected {exp} - scrip master changed?")
        out.append(Instrument(u, spot["exch_seg"], str(spot["token"]), "spot"))
        if volume_proxy == "futures":
            fut = near_month_future(rows, u, FUTURES_EXCHANGE[spot["exch_seg"]], today)
            if fut is None:
                log.warning("%s: no near-month future found - volume features disabled", u)
                continue
            out.append(Instrument(u, fut["exch_seg"], str(fut["token"]), "volume_proxy"))
            log.info("%s volume proxy: %s (%s) expiry %s", u, fut["symbol"], fut["token"], fut["expiry"])
    return out


def ws_type(exchange: str) -> str:
    return WS_EXCHANGE_TYPE[exchange]
