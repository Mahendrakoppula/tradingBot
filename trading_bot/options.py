import datetime as dt
from dataclasses import dataclass

# Verified against a live download of OpenAPIScripMaster.json (2026-09-07):
# strike is the actual strike price * 100 (e.g. "2300000.000000" -> 23000.0).
STRIKE_SCALE = 100.0


@dataclass
class OptionContract:
    token: str
    tradingsymbol: str
    name: str
    expiry: dt.date
    strike: float
    option_type: str  # "CE" or "PE"
    lotsize: int
    freeze_qty: int
    exchange: str  # "NFO" or "BFO"


def _parse_expiry(raw: str) -> dt.date:
    return dt.datetime.strptime(raw, "%d%b%Y").date()


class OptionChain:
    """Filters the scrip master down to one underlying's F&O option contracts.

    Verified against a live scrip master dump (2026-09-07): exch_seg is a plain
    exchange code ("NFO"/"BFO"), NOT the "nse_cm"-style naming the docs use
    elsewhere for WebSocket subscriptions (see ws_market.EXCH_SEG_TO_WS_TYPE).
    """

    def __init__(self, instruments: list[dict], underlying: str, exchange: str = "NFO"):
        self.underlying = underlying.upper()
        self.exchange = exchange
        rows = [
            row
            for row in instruments
            if str(row.get("name", "")).upper() == self.underlying
            and row.get("instrumenttype") in ("OPTIDX", "OPTSTK")
            and row.get("exch_seg") == exchange
        ]
        self.contracts = [self._to_contract(row) for row in rows]

    def _to_contract(self, row: dict) -> OptionContract:
        symbol = row["symbol"]
        return OptionContract(
            token=row["token"],
            tradingsymbol=symbol,
            name=row["name"],
            expiry=_parse_expiry(row["expiry"]),
            strike=float(row["strike"]) / STRIKE_SCALE,
            option_type=symbol[-2:],
            lotsize=int(row["lotsize"]),
            freeze_qty=int(row["freeze_qty"]),
            exchange=self.exchange,
        )

    def expiries(self) -> list[dt.date]:
        return sorted({c.expiry for c in self.contracts})

    def nearest_expiry_within(self, today: dt.date, dte_min: int, dte_max: int) -> dt.date | None:
        candidates = [e for e in self.expiries() if dte_min <= (e - today).days <= dte_max]
        return min(candidates) if candidates else None

    def for_expiry(self, expiry: dt.date) -> list[OptionContract]:
        return [c for c in self.contracts if c.expiry == expiry]

    def nearest_strike(self, expiry: dt.date, option_type: str, target_strike: float) -> OptionContract:
        candidates = [c for c in self.for_expiry(expiry) if c.option_type == option_type]
        if not candidates:
            raise LookupError(f"No {option_type} contracts for {self.underlying} {expiry}")
        return min(candidates, key=lambda c: abs(c.strike - target_strike))


def find_spot_instrument(instruments: list[dict], underlying: str) -> dict:
    """Resolves an underlying name to its spot-price instrument (index or equity).

    Verified against a live scrip master dump (2026-09-07):
    - Index: instrumenttype "AMXIDX" (e.g. NIFTY -> token 99926000, exch_seg NSE;
      BANKNIFTY -> token 99926009).
    - Equity: instrumenttype "" with tradingsymbol "<NAME>-EQ" on exch_seg NSE.
    """
    underlying = underlying.upper()
    for row in instruments:
        if (
            str(row.get("name", "")).upper() == underlying
            and row.get("instrumenttype") == "AMXIDX"
            and row.get("exch_seg") == "NSE"
        ):
            return row
    for row in instruments:
        if (
            str(row.get("name", "")).upper() == underlying
            and row.get("instrumenttype") == ""
            and row.get("exch_seg") == "NSE"
            and str(row.get("symbol", "")).endswith("-EQ")
        ):
            return row
    raise LookupError(f"No spot instrument found for {underlying}")
