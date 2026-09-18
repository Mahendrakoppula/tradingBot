"""Option candidate cache (spec §20) and the quote/Greeks snapshot it is
built from. Keeps, per underlying, a small band of strikes around spot
for the nearest few expiries, refreshed on a cadence by the loop with ONE
batched FULL quote call (up to 50 tokens) plus one Greeks call for NSE
names. SENSEX (BFO) has no Greeks endpoint: IV is implied from the mid
price and Greeks come from Black-Scholes (`engine/bs.py`), labelled
`greeks_source="model"` so nobody mistakes them for broker data.

The cache is a PRELIMINARY filter only (§20): the execution gate re-quotes
the chosen contract immediately before any order.
"""
import datetime as dt
from dataclasses import dataclass, field

from trading_bot.engine import bs
from trading_bot.options import OptionChain, OptionContract


@dataclass
class OptionQuote:
    contract: OptionContract
    ts: dt.datetime
    ltp: float
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    volume: int
    oi: int
    iv: float | None = None  # decimal, e.g. 0.14
    delta: float | None = None
    gamma: float | None = None
    theta_per_day: float | None = None  # premium points per calendar day, negative for a long
    vega: float | None = None
    greeks_source: str = "none"  # "broker" | "model" | "none"
    spot: float | None = None

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return 0.5 * (self.bid + self.ask)
        return self.ltp

    @property
    def spread(self) -> float:
        return (self.ask - self.bid) if (self.bid > 0 and self.ask > 0) else float("inf")

    @property
    def spread_pct(self) -> float:
        m = self.mid
        return (self.spread / m * 100.0) if m > 0 and self.spread != float("inf") else float("inf")

    @property
    def theta_pct_of_premium(self) -> float | None:
        if self.theta_per_day is None or self.mid <= 0:
            return None
        return abs(self.theta_per_day) / self.mid

    @property
    def moneyness(self) -> float | None:
        """(strike - spot)/spot signed so that positive = OTM for the contract's side."""
        if not self.spot:
            return None
        d = (self.contract.strike - self.spot) / self.spot
        return d if self.contract.option_type == "CE" else -d

    def as_dict(self) -> dict:
        c = self.contract
        return {"token": c.token, "symbol": c.tradingsymbol, "expiry": c.expiry.isoformat(), "strike": c.strike,
                "option_type": c.option_type, "lotsize": c.lotsize, "ltp": self.ltp, "bid": self.bid, "ask": self.ask,
                "bid_qty": self.bid_qty, "ask_qty": self.ask_qty, "volume": self.volume, "oi": self.oi, "iv": self.iv,
                "delta": self.delta, "gamma": self.gamma, "theta": self.theta_per_day, "vega": self.vega,
                "greeks_source": self.greeks_source, "spot": self.spot, "spread_pct": None if self.spread_pct == float("inf") else round(self.spread_pct, 3)}


def parse_full_quote(contract: OptionContract, row: dict, ts: dt.datetime, spot: float | None) -> OptionQuote:
    depth = row.get("depth") or {}
    buy = [l for l in (depth.get("buy") or []) if float(l.get("quantity", 0) or 0) > 0]
    sell = [l for l in (depth.get("sell") or []) if float(l.get("quantity", 0) or 0) > 0]
    bid = float(buy[0]["price"]) if buy else 0.0
    ask = float(sell[0]["price"]) if sell else 0.0
    return OptionQuote(
        contract=contract, ts=ts, ltp=float(row.get("ltp") or 0.0), bid=bid, ask=ask,
        bid_qty=int(float(buy[0]["quantity"])) if buy else 0, ask_qty=int(float(sell[0]["quantity"])) if sell else 0,
        volume=int(float(row.get("tradeVolume") or 0)), oi=int(float(row.get("opnInterest") or 0)), spot=spot,
    )


def apply_broker_greeks(q: OptionQuote, greeks_rows: list[dict]) -> bool:
    """Attach the broker's Greeks row for this strike/type; True on match."""
    for g in greeks_rows:
        try:
            if abs(float(g.get("strikePrice", -1)) - q.contract.strike) < 1e-6 and g.get("optionType") == q.contract.option_type:
                q.delta = float(g["delta"]); q.gamma = float(g["gamma"]); q.theta_per_day = float(g["theta"])
                q.vega = float(g["vega"]); q.iv = float(g["impliedVolatility"]) / 100.0
                q.greeks_source = "broker"
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def apply_model_greeks(q: OptionQuote, days_to_expiry: int, session_elapsed: float) -> bool:
    """Black-Scholes IV from the mid, then Greeks; False when no IV solves."""
    if not q.spot or q.mid <= 0:
        return False
    t = bs.years_to_expiry(days_to_expiry, session_elapsed)
    iv = bs.implied_vol(q.mid, q.spot, q.contract.strike, t, q.contract.option_type)
    if iv is None:
        return False
    g = bs.greeks(q.spot, q.contract.strike, t, iv, q.contract.option_type)
    q.iv, q.delta, q.gamma, q.theta_per_day, q.vega = iv, g.delta, g.gamma, g.theta_per_day, g.vega
    q.greeks_source = "model"
    return True


@dataclass(frozen=True)
class CacheParams:
    strikes_each_side: int = 6
    expiries: int = 2  # nearest N expiries kept
    dte_min: int = 0
    dte_max: int = 14
    refresh_seconds: int = 60
    max_quote_age_seconds: int = 120  # older than this, the cache is not a valid prefilter


@dataclass
class ChainCache:
    """Per-underlying candidate contracts + their latest quotes."""
    underlying: str
    chain: OptionChain
    params: CacheParams = field(default_factory=CacheParams)
    quotes: dict[str, OptionQuote] = field(default_factory=dict)  # token -> quote
    last_refresh: dt.datetime | None = None

    def candidates(self, spot: float, today: dt.date) -> list[OptionContract]:
        """The contracts worth quoting: nearest `expiries` within the DTE
        window, `strikes_each_side` around spot, both CE and PE."""
        exps = [e for e in self.chain.expiries() if self.params.dte_min <= (e - today).days <= self.params.dte_max][: self.params.expiries]
        out: list[OptionContract] = []
        for exp in exps:
            for ot in ("CE", "PE"):
                cs = sorted((c for c in self.chain.for_expiry(exp) if c.option_type == ot), key=lambda c: c.strike)
                if not cs:
                    continue
                strikes = [c.strike for c in cs]
                # index of the strike nearest spot
                k = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
                lo, hi = max(0, k - self.params.strikes_each_side), min(len(cs), k + self.params.strikes_each_side + 1)
                out.extend(cs[lo:hi])
        return out

    def refresh(self, rest, spot: float, now: dt.datetime, *, session_elapsed: float, greeks_for_nse: bool = True) -> int:
        """One batched FULL quote (<=50 tokens per call) + Greeks per
        expiry (NSE only); model Greeks elsewhere. Returns quotes stored."""
        today = now.date()
        cands = self.candidates(spot, today)
        if not cands:
            self.quotes = {}
            self.last_refresh = now
            return 0
        by_token = {c.token: c for c in cands}
        exchange = cands[0].exchange
        fetched: list[dict] = []
        tokens = list(by_token)
        for i in range(0, len(tokens), 50):
            data = rest.get_quote("FULL", {exchange: tokens[i:i + 50]}) or {}
            fetched.extend(data.get("fetched") or [])
        greeks_by_expiry: dict[dt.date, list[dict]] = {}
        if greeks_for_nse and exchange == "NFO":
            for exp in sorted({c.expiry for c in cands}):
                try:
                    greeks_by_expiry[exp] = rest.get_option_greeks(self.underlying, exp.strftime("%d%b%Y").upper()) or []
                except Exception:  # noqa: BLE001 - Greeks are optional; model fallback below
                    greeks_by_expiry[exp] = []
        quotes: dict[str, OptionQuote] = {}
        for row in fetched:
            c = by_token.get(str(row.get("symbolToken")))
            if c is None:
                continue
            q = parse_full_quote(c, row, now, spot)
            if not apply_broker_greeks(q, greeks_by_expiry.get(c.expiry, [])):
                apply_model_greeks(q, (c.expiry - today).days, session_elapsed)
            quotes[c.token] = q
        self.quotes = quotes
        self.last_refresh = now
        return len(quotes)

    def fresh(self, now: dt.datetime) -> bool:
        return self.last_refresh is not None and (now - self.last_refresh).total_seconds() <= self.params.max_quote_age_seconds

    def for_side(self, option_type: str) -> list[OptionQuote]:
        return [q for q in self.quotes.values() if q.contract.option_type == option_type]
