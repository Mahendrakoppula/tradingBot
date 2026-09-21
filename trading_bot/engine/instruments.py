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

WS_EXCHANGE_TYPE = {"NSE": "nse_cm", "BSE": "bse_cm", "NFO": "nse_fo", "BFO": "bse_fo", "MCX": "mcx_fo"}
FUTURES_EXCHANGE = {"NSE": "NFO", "BSE": "BFO"}
# where an underlying's options trade, keyed by the exchange its price reference ticks on
OPTIONS_EXCHANGE = {"NSE": "NFO", "BSE": "BFO", "MCX": "MCX"}
EXPECTED_SPOT = {"NIFTY": ("99926000", "NSE"), "BANKNIFTY": ("99926009", "NSE"), "SENSEX": ("99919000", "BSE")}

# MCX commodity spike (shadow-only): the price reference is the near-month
# FUTURE itself - MCX's "COMDTY" spot row updates a few times a day and does
# not tick - and it carries its own volume, so there is no separate proxy.
# CRUDEOIL (100 bbl) and CRUDEOILM (10 bbl) both list OPTFUT options
# (verified against the scrip master 2026-09-21: monthly futures, options
# expiring ~4 sessions before the future, strike step 50).
COMMODITY_UNDERLYINGS: frozenset[str] = frozenset({"CRUDEOIL", "CRUDEOILM"})
FUTURE_TYPES = {"NFO": "FUTIDX", "BFO": "FUTIDX", "MCX": "FUTCOM"}


def _parse_expiry(raw: str) -> dt.date:
    return dt.datetime.strptime(raw, "%d%b%Y").date()


def near_month_future(rows: list[dict], underlying: str, exchange: str, today: dt.date,
                      min_days_to_expiry: int = 0) -> dict | None:
    """The nearest future expiring at least `min_days_to_expiry` days out
    (commodities roll a couple of sessions before expiry - the expiring
    contract is where delivery intentions, not price discovery, live)."""
    cands = []
    ftype = FUTURE_TYPES.get(exchange, "FUTIDX")
    for r in rows:
        if str(r.get("name", "")).upper() != underlying or r.get("instrumenttype") != ftype or r.get("exch_seg") != exchange:
            continue
        try:
            exp = _parse_expiry(r["expiry"])
        except (KeyError, ValueError):
            continue
        if (exp - today).days >= min_days_to_expiry:
            cands.append((exp, r))
    if not cands:
        return None
    return min(cands, key=lambda x: x[0])[1]


def is_commodity(underlying: str) -> bool:
    return underlying.upper() in COMMODITY_UNDERLYINGS


def resolve_instruments(rows: list[dict], underlyings: tuple[str, ...], today: dt.date,
                        volume_proxy: str = "futures", future_roll_days: int = 2) -> list[Instrument]:
    out: list[Instrument] = []
    for u in underlyings:
        if is_commodity(u):
            fut = near_month_future(rows, u, "MCX", today, min_days_to_expiry=future_roll_days)
            if fut is None:
                raise RuntimeError(f"{u}: no MCX future expiring >= {future_roll_days} days out in the scrip master")
            out.append(Instrument(u, "MCX", str(fut["token"]), "spot"))
            log.info("%s price reference: near-month future %s (%s) expiry %s, lot %s", u, fut["symbol"], fut["token"],
                     fut["expiry"], fut.get("lotsize"))
            continue
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
