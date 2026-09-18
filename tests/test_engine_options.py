import datetime as dt
import math

from trading_bot.costs import CostRates
from trading_bot.engine import bs
from trading_bot.engine.option_chain import (
    CacheParams,
    ChainCache,
    OptionQuote,
    apply_broker_greeks,
    apply_model_greeks,
    parse_full_quote,
)
from trading_bot.engine.option_select import SelectParams, decay_filter, select
from trading_bot.options import OptionChain, OptionContract
from trading_bot.timeutil import IST

TODAY = dt.date(2026, 9, 16)
NOW = dt.datetime(2026, 9, 16, 10, 30, tzinfo=IST)
SPOT = 25000.0


# --- Black-Scholes ------------------------------------------------------------------


def test_bs_put_call_parity_and_greek_signs():
    t = 7 / 365
    c = bs.price(SPOT, 25000, t, 0.14, "CE")
    p = bs.price(SPOT, 25000, t, 0.14, "PE")
    assert abs((c - p) - (SPOT - 25000 * math.exp(-0.065 * t))) < 1e-6
    g = bs.greeks(SPOT, 25000, t, 0.14, "CE")
    assert 0.5 < g.delta < 0.6 and g.gamma > 0 and g.theta_per_day < 0 and g.vega > 0
    gp = bs.greeks(SPOT, 25000, t, 0.14, "PE")
    assert -0.5 < gp.delta < -0.4


def test_bs_implied_vol_round_trips_and_rejects_bad_prices():
    t = 5 / 365
    px = bs.price(SPOT, 25200, t, 0.16, "CE")
    iv = bs.implied_vol(px, SPOT, 25200, t, "CE")
    assert abs(iv - 0.16) < 1e-3
    assert bs.implied_vol(0.01, SPOT, 24000, t, "CE") is None  # below intrinsic
    assert bs.implied_vol(px, SPOT, 25200, 0.0, "CE") is None


def test_bs_expiry_handling():
    assert bs.price(SPOT, 24900, 0.0, 0.2, "CE") == 100.0
    assert bs.greeks(SPOT, 24900, 0.0, 0.2, "CE").delta == 1.0
    assert bs.years_to_expiry(0, 1.0) == 0.0
    assert bs.years_to_expiry(0, 0.0) > 0 and bs.years_to_expiry(3, 0.5) > bs.years_to_expiry(2, 0.5)


# --- chain cache --------------------------------------------------------------------


def _rows(underlying="NIFTY", exchange="NFO", expiries=("23SEP2026", "30SEP2026"), step=50, n=20, lot=75):
    rows = []
    tok = 1000
    for e in expiries:
        for k in range(-n, n + 1):
            strike = SPOT + k * step
            for ot in ("CE", "PE"):
                tok += 1
                rows.append({"token": str(tok), "symbol": f"{underlying}{e}{int(strike)}{ot}", "name": underlying,
                             "expiry": e, "strike": str(strike * 100), "instrumenttype": "OPTIDX", "exch_seg": exchange,
                             "lotsize": str(lot), "freeze_qty": str(lot * 24)})
    return rows


class FakeRest:
    def __init__(self, iv=0.14, wide_tokens=(), no_depth_tokens=(), greeks=True):
        self.iv, self.wide, self.nodepth, self.greeks_on = iv, set(wide_tokens), set(no_depth_tokens), greeks
        self.quote_calls, self.greek_calls = [], []
        self.contracts_by_token = {}

    def get_quote(self, mode, exchange_tokens):
        assert mode == "FULL"
        fetched = []
        for exch, toks in exchange_tokens.items():
            assert len(toks) <= 50
            self.quote_calls.append(len(toks))
            for t in toks:
                c = self.contracts_by_token[t]
                dte = (c.expiry - TODAY).days
                mid = max(1.0, bs.price(SPOT, c.strike, bs.years_to_expiry(dte, 0.3), self.iv, c.option_type))
                half = mid * (0.05 if t in self.wide else 0.005)
                depth = {} if t in self.nodepth else {"buy": [{"price": round(mid - half, 2), "quantity": 500, "orders": 5}],
                                                       "sell": [{"price": round(mid + half, 2), "quantity": 400, "orders": 4}]}
                fetched.append({"exchange": exch, "symbolToken": t, "tradingSymbol": c.tradingsymbol, "ltp": round(mid, 2),
                                "tradeVolume": 20000, "opnInterest": 80000, "depth": depth})
        return {"fetched": fetched, "unfetched": []}

    def get_option_greeks(self, name, expirydate):
        self.greek_calls.append(expirydate)
        if not self.greeks_on:
            return []
        out = []
        for c in self.contracts_by_token.values():
            if c.expiry.strftime("%d%b%Y").upper() != expirydate:
                continue
            g = bs.greeks(SPOT, c.strike, bs.years_to_expiry((c.expiry - TODAY).days, 0.3), self.iv, c.option_type)
            out.append({"name": name, "expiry": expirydate, "strikePrice": str(c.strike), "optionType": c.option_type,
                        "delta": g.delta, "gamma": g.gamma, "theta": g.theta_per_day, "vega": g.vega,
                        "impliedVolatility": self.iv * 100, "tradeVolume": 1})
        return out


def _cache(rows=None, **params) -> tuple[ChainCache, FakeRest]:
    rows = rows or _rows()
    chain = OptionChain(rows, "NIFTY", rows[0]["exch_seg"])
    rest = FakeRest()
    rest.contracts_by_token = {c.token: c for c in chain.contracts}
    return ChainCache("NIFTY", chain, CacheParams(**params)), rest


def test_candidates_band_around_spot_for_nearest_expiries():
    cache, _ = _cache(strikes_each_side=3, expiries=1)
    cands = cache.candidates(SPOT, TODAY)
    assert len(cands) == 2 * 7  # CE+PE x (3 each side + ATM)
    assert {c.expiry for c in cands} == {dt.date(2026, 9, 23)}
    strikes = sorted({c.strike for c in cands})
    assert strikes == [SPOT + k * 50 for k in range(-3, 4)]
    assert cache.candidates(SPOT, dt.date(2026, 10, 5)) == []  # both expiries in the past


def test_refresh_batches_quotes_and_attaches_broker_greeks():
    cache, rest = _cache(strikes_each_side=6, expiries=2)
    n = cache.refresh(rest, SPOT, NOW, session_elapsed=0.3)
    assert n == 2 * 2 * 13 == 52
    assert rest.quote_calls == [50, 2] and len(rest.greek_calls) == 2
    q = next(q for q in cache.quotes.values() if q.contract.strike == SPOT and q.contract.option_type == "CE"
             and q.contract.expiry == dt.date(2026, 9, 23))
    assert q.greeks_source == "broker" and 0.5 < q.delta < 0.6 and q.iv == 0.14
    assert q.bid < q.ltp < q.ask and q.spread_pct < 1.5 and q.oi == 80000
    assert cache.fresh(NOW) and not cache.fresh(NOW + dt.timedelta(minutes=5))
    assert len(cache.for_side("PE")) == 26


def test_refresh_uses_model_greeks_when_broker_has_none():
    rows = _rows(underlying="SENSEX", exchange="BFO", expiries=("18SEP2026",), step=100, lot=20)
    chain = OptionChain(rows, "SENSEX", "BFO")
    rest = FakeRest()
    rest.contracts_by_token = {c.token: c for c in chain.contracts}
    cache = ChainCache("SENSEX", chain, CacheParams(strikes_each_side=2, expiries=1))
    assert cache.refresh(rest, SPOT, NOW, session_elapsed=0.3) == 10
    assert rest.greek_calls == []  # BFO: no Greeks endpoint call
    q = next(q for q in cache.quotes.values() if q.contract.strike == SPOT and q.contract.option_type == "CE")
    assert q.greeks_source == "model" and abs(q.iv - 0.14) < 0.01 and 0.5 < q.delta < 0.6 and q.theta_per_day < 0


def test_parse_full_quote_without_depth():
    c = OptionContract("1", "X", "NIFTY", TODAY, SPOT, "CE", 75, 1800, "NFO")
    q = parse_full_quote(c, {"ltp": "100", "tradeVolume": "10", "opnInterest": "20", "depth": {}}, NOW, SPOT)
    assert q.bid == 0 and q.ask == 0 and q.mid == 100.0 and q.spread == float("inf") and q.spread_pct == float("inf")
    assert not apply_broker_greeks(q, [{"strikePrice": "1", "optionType": "CE"}])
    assert not apply_model_greeks(q, 0, 1.0)  # no time left -> no IV


# --- selection ----------------------------------------------------------------------


def _quotes(**over) -> list[OptionQuote]:
    cache, rest = _cache(strikes_each_side=6, expiries=2)
    for k, v in over.items():
        setattr(rest, k, v)
    cache.refresh(rest, SPOT, NOW, session_elapsed=0.3)
    return list(cache.quotes.values())


def test_select_prefers_delta_band_liquidity_and_tight_spread():
    sel = select(_quotes(), "CE", TODAY, capital=50000)
    assert sel.ok and sel.chosen.contract.option_type == "CE"
    assert 0.30 <= abs(sel.chosen.delta) <= 0.65
    assert sel.chosen.contract.expiry == dt.date(2026, 9, 23)  # 7 DTE beats 14 on time fit at equal everything else
    reasons = {r.reason_code for r in sel.rejections}
    assert "delta_too_low" in reasons and "delta_too_high" in reasons
    assert sel.ranked[0][0] == sel.chosen.contract.tradingsymbol and len(sel.ranked) >= 3
    assert abs(sum(sel.breakdown.values()) - sel.score) < 1e-6


def test_select_rejects_everything_when_nothing_is_acceptable():
    sel = select(_quotes(), "CE", TODAY, capital=50000, params=SelectParams(max_spread_pct=0.01))
    assert not sel.ok and all(r.reason_code == "spread_too_wide" for r in sel.rejections if r.reason_code != "delta_too_low"
                              and r.reason_code != "delta_too_high")
    # capital too small for one lot
    sel2 = select(_quotes(), "CE", TODAY, capital=1000)
    assert not sel2.ok and "lot_too_expensive_for_capital" in {r.reason_code for r in sel2.rejections}


def test_select_expiry_day_off_by_default_and_far_otm_rejected():
    rows = _rows(expiries=("16SEP2026",), n=30)
    chain = OptionChain(rows, "NIFTY", "NFO")
    rest = FakeRest()
    rest.contracts_by_token = {c.token: c for c in chain.contracts}
    cache = ChainCache("NIFTY", chain, CacheParams(strikes_each_side=20, expiries=1, dte_min=0))
    cache.refresh(rest, SPOT, NOW, session_elapsed=0.3)
    sel = select(list(cache.quotes.values()), "CE", TODAY, capital=50000)
    assert not sel.ok and {r.reason_code for r in sel.rejections} <= {"expiry_day_not_validated", "delta_too_low", "delta_too_high", "premium_too_small", "no_greeks", "too_far_otm", "theta_too_heavy"}
    # expiry-day ATM theta is a few HUNDRED percent of premium per day - only an absurd
    # theta ceiling lets it through, which is the point of the separate validation rule
    sel2 = select(list(cache.quotes.values()), "CE", TODAY, capital=50000, params=SelectParams(expiry_day_allowed=True, max_theta_pct=5.0))
    assert sel2.ok and sel2.chosen.contract.strike == SPOT


def test_decay_filter_requires_gain_to_beat_all_costs():
    q = select(_quotes(), "CE", TODAY, capital=50000).chosen
    rates = CostRates()
    good = decay_filter(q, expected_underlying_move=60.0, cost_rates=rates)
    assert good.ok and good.net_expected_gain > 0 and good.expected_option_gain > good.theta_cost + good.spread_cost
    assert abs(good.net_expected_reward_rupees - good.net_expected_gain * q.contract.lotsize) < 1.0
    tiny = decay_filter(q, expected_underlying_move=2.0, cost_rates=rates)
    assert not tiny.ok and tiny.reason_code == "gain_below_costs"
    none = decay_filter(q, expected_underlying_move=0.0, cost_rates=rates)
    assert not none.ok and none.reason_code == "no_expected_gain"
    assert tiny.txn_cost_points > 0 and tiny.spread_cost > 0 and tiny.theta_cost > 0
