"""Option selection (spec §19) and the decay filter (§24).

Evaluates every cached contract on the thesis side and picks the best
by a transparent score over delta, spread, liquidity, theta burden,
moneyness and time to expiry - never "ATM by default", "nearest expiry
by default" or "cheapest" (§19). Each rejected contract carries its
reason so the journal can show why the chosen one won. When nothing
passes, the setup is rejected (§19: "must be rejected if no acceptable
option exists").

The decay filter is the gate the spec spells out (§24): expected option
gain must exceed theta + spread + slippage + transaction costs over the
expected hold, else NO TRADE.
"""
from dataclasses import dataclass, field

from trading_bot.costs import CostRates
from trading_bot.engine.expected_move import option_response
from trading_bot.engine.option_chain import OptionQuote


@dataclass(frozen=True)
class SelectParams:
    delta_min: float = 0.30
    delta_max: float = 0.65
    delta_target: float = 0.45
    max_spread_pct: float = 2.0
    min_oi: int = 5000
    min_volume: int = 500
    max_theta_pct: float = 0.10  # |theta|/premium per day - weekly index ATM runs 5-8%
    dte_min: int = 0
    dte_max: int = 14
    expiry_day_allowed: bool = False  # §24: expiry-day rules must be validated separately - off by default
    max_otm_pct: float = 0.02  # "no lottery-style far OTM" (§19/§92 #28)
    # Where on the moneyness axis the selector AIMS. None = the index default: at-the-money is
    # ideal and anything in-the-money scores a flat 0.8. A value (e.g. 0.015 = 1.5% OTM) makes the
    # score peak there instead and fall away on both sides - "slightly OTM", which on MCX crude
    # buys a cheaper lot without reaching for a lottery ticket. max_otm_pct still hard-caps it.
    otm_target_pct: float | None = None
    min_premium: float = 5.0
    max_premium_pct_of_capital: float = 0.35  # one lot must not eat the account
    hold_fraction_of_day: float = 0.25
    slippage_pct_of_spread: float = 0.5  # expect to pay half the spread each side


@dataclass
class OptionRejection:
    token: str
    symbol: str
    reason_code: str
    detail: str = ""


@dataclass
class Selection:
    chosen: OptionQuote | None
    score: float
    breakdown: dict = field(default_factory=dict)
    rejections: list[OptionRejection] = field(default_factory=list)
    ranked: list[tuple[str, float]] = field(default_factory=list)  # (symbol, score)

    @property
    def ok(self) -> bool:
        return self.chosen is not None


def _hard_filters(q: OptionQuote, today_dte: int, p: SelectParams, capital: float) -> str | None:
    if q.bid <= 0 or q.ask <= 0:
        return "no_two_sided_quote"
    if q.mid < p.min_premium:
        return "premium_too_small"
    if q.spread_pct > p.max_spread_pct:
        return "spread_too_wide"
    if q.oi < p.min_oi:
        return "open_interest_too_low"
    if q.volume < p.min_volume:
        return "volume_too_low"
    if q.delta is None:
        return "no_greeks"
    d = abs(q.delta)
    if d < p.delta_min:
        return "delta_too_low"
    if d > p.delta_max:
        return "delta_too_high"
    if today_dte < p.dte_min or today_dte > p.dte_max:
        return "expiry_outside_window"
    if today_dte == 0 and not p.expiry_day_allowed:
        return "expiry_day_not_validated"
    m = q.moneyness
    if m is not None and m > p.max_otm_pct:
        return "too_far_otm"
    tp = q.theta_pct_of_premium
    if tp is not None and tp > p.max_theta_pct:
        return "theta_too_heavy"
    if q.ask * q.contract.lotsize > p.max_premium_pct_of_capital * capital:
        return "lot_too_expensive_for_capital"
    return None


def _score(q: OptionQuote, dte: int, p: SelectParams) -> tuple[float, dict]:
    d = abs(q.delta or 0.0)
    delta_fit = 1.0 - min(1.0, abs(d - p.delta_target) / (p.delta_max - p.delta_min))
    spread_fit = 1.0 - min(1.0, q.spread_pct / p.max_spread_pct)
    liq = min(1.0, q.oi / (4 * p.min_oi)) * 0.6 + min(1.0, q.volume / (4 * p.min_volume)) * 0.4
    tp = q.theta_pct_of_premium if q.theta_pct_of_premium is not None else p.max_theta_pct
    theta_fit = 1.0 - min(1.0, tp / p.max_theta_pct)
    # time: neither expiring today nor so far that gamma is dead; peak around 3-7 DTE
    time_fit = 1.0 - min(1.0, abs(dte - 5) / 9.0)
    m = q.moneyness or 0.0
    if p.otm_target_pct is None:
        money_fit = 1.0 - min(1.0, abs(m) / p.max_otm_pct) if m > 0 else 0.8  # slightly ITM is fine, far OTM is not
    else:
        money_fit = 1.0 - min(1.0, abs(m - p.otm_target_pct) / max(p.max_otm_pct, 1e-6))
    parts = {"delta": 0.30 * delta_fit, "spread": 0.20 * spread_fit, "liquidity": 0.15 * liq, "theta": 0.15 * theta_fit,
             "time": 0.10 * time_fit, "moneyness": 0.10 * money_fit}
    return round(sum(parts.values()), 4), {k: round(v, 4) for k, v in parts.items()}


def select(quotes: list[OptionQuote], option_type: str, today, capital: float,
           params: SelectParams | None = None) -> Selection:
    p = params or SelectParams()
    rejections: list[OptionRejection] = []
    scored: list[tuple[float, OptionQuote, dict]] = []
    for q in quotes:
        if q.contract.option_type != option_type:
            continue
        dte = (q.contract.expiry - today).days
        why = _hard_filters(q, dte, p, capital)
        if why:
            rejections.append(OptionRejection(q.contract.token, q.contract.tradingsymbol, why))
            continue
        s, br = _score(q, dte, p)
        scored.append((s, q, br))
    if not scored:
        return Selection(None, 0.0, {}, rejections, [])
    scored.sort(key=lambda t: (-t[0], t[1].contract.tradingsymbol))
    best_s, best_q, best_br = scored[0]
    return Selection(best_q, best_s, best_br, rejections, [(q.contract.tradingsymbol, s) for s, q, _ in scored])


# --- §24 decay filter --------------------------------------------------------------------------


@dataclass(frozen=True)
class DecayVerdict:
    ok: bool
    reason_code: str | None
    expected_option_gain: float  # premium points, before costs
    theta_cost: float
    spread_cost: float  # points, both sides
    slippage_cost: float
    txn_cost_points: float  # brokerage/taxes per unit, in premium points
    net_expected_gain: float  # points after everything
    net_expected_reward_rupees: float  # for one lot


def decay_filter(q: OptionQuote, expected_underlying_move: float, cost_rates: CostRates,
                 params: SelectParams | None = None, quantity: int | None = None) -> DecayVerdict:
    p = params or SelectParams()
    qty = quantity or q.contract.lotsize
    gain = option_response(expected_underlying_move, q.mid, q.delta or 0.0, q.gamma, theta_per_day=0.0)
    theta_cost = abs(q.theta_per_day or 0.0) * p.hold_fraction_of_day
    spread_cost = q.spread if q.spread != float("inf") else q.mid  # pay the spread once per round trip
    slippage = p.slippage_pct_of_spread * spread_cost
    exit_est = q.mid + gain
    txn = cost_rates.option_cost(q.mid, max(0.05, exit_est), qty) / qty
    net = gain - theta_cost - spread_cost - slippage - txn
    ok = net > 0
    reason = None if ok else ("gain_below_costs" if gain > 0 else "no_expected_gain")
    return DecayVerdict(ok, reason, round(gain, 2), round(theta_cost, 2), round(spread_cost, 2), round(slippage, 2),
                        round(txn, 2), round(net, 2), round(net * qty, 2))
