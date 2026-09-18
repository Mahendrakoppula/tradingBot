"""Signal scoring (spec §16) and opportunity ranking (§15, §35).

Score is a 0-100 CONTEXT score built from nine components with the
spec's suggested caps, minus penalties. It is an ordering device, not a
probability ("Score is not probability unless calibrated", §16) - the
risk engine (§25-30) decides whether anything is tradeable; scoring only
decides which candidate is looked at first.

Trend alignment influences ROUTING (families are gated on it) and gets a
modest share of the MTF component here - deliberately not counted again
as its own component (§16 "must not be double-counted excessively").
"""
from dataclasses import dataclass, field

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.strategies.base import Candidate

CAPS = {
    "price_action": 20, "structure": 15, "key_location": 15, "mtf_alignment": 15, "volume": 10,
    "momentum": 10, "volatility": 5, "liquidity_execution": 5, "option_quality": 5,
}

PENALTIES = {
    "conflicting_timeframes": 10, "counter_trend": 15, "weak_structure": 5, "weak_volume": 5, "poor_location": 8,
    "nearby_opposing_level": 10, "excessive_spread": 10, "poor_liquidity": 10, "insufficient_expected_movement": 15,
    "excessive_theta": 8, "extension": 10, "regime_mismatch": 10,
}

_BULL = ("STRONG_BULL", "BULL", "WEAK_BULL")
_BEAR = ("STRONG_BEAR", "BEAR", "WEAK_BEAR")


@dataclass(frozen=True)
class ExternalInputs:
    """Facts scoring cannot read from the context: they come from the option
    and expected-move engines (M2 steps 3-4). All optional; a missing value
    scores its component at zero and adds no penalty (unknown != bad)."""
    spread_pct: float | None = None
    open_interest: int | None = None
    theta_pct_of_premium: float | None = None  # |theta| per day / premium
    remaining_move_atr: float | None = None  # realistic remaining move in ATRs
    move_consumed_atr: float | None = None  # how far the move has already run
    max_spread_pct: float = 2.0
    min_open_interest: int = 5000
    min_remaining_move_atr: float = 0.75
    max_theta_pct: float = 0.06


@dataclass
class Score:
    total: int
    components: dict[str, int] = field(default_factory=dict)
    penalties: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def raw(self) -> int:
        return sum(self.components.values())


def _clamp(v: float, cap: int) -> int:
    return int(round(max(0.0, min(float(cap), v))))


def _price_action(ctx: ContextSnapshot, cand: Candidate) -> tuple[int, list[str]]:
    labels = set(ctx.price_action.get("labels", []))
    an = ctx.price_action.get("anatomy") or {}
    pts, notes = 0.0, []
    with_dir = {"bullish_pin", "bullish_engulfing", "rejection_of_low", "morning_star"} if cand.direction == "up" else \
               {"bearish_pin", "bearish_engulfing", "rejection_of_high", "evening_star"}
    hits = labels & with_dir
    if hits:
        pts += 10
        notes.append("pattern:" + ",".join(sorted(hits)))
    if "displacement" in labels and an.get("bullish", True) == (cand.direction == "up"):
        pts += 6
    close_loc = an.get("close_loc")
    if close_loc is not None:
        strength = close_loc if cand.direction == "up" else 1.0 - close_loc
        pts += 4 * strength  # closed near the extreme in our direction
    if "doji" in labels or "inside_bar" in labels:
        pts -= 3
    return _clamp(pts, CAPS["price_action"]), notes


def _structure(ctx: ContextSnapshot, cand: Candidate) -> tuple[int, list[str]]:
    st = ctx.structure or {}
    ev = st.get("last_event") or {}
    pts, notes = 0.0, []
    with_dir = ("bos_up", "choch_up") if cand.direction == "up" else ("bos_down", "choch_down")
    against = ("bos_down", "choch_down") if cand.direction == "up" else ("bos_up", "choch_up")
    if ev.get("kind") in with_dir:
        age = ctx.bar_index - int(ev.get("index", ctx.bar_index))
        pts += 9 if age <= 3 else 5
        notes.append(ev["kind"])
    elif ev.get("kind") in against:
        pts -= 4
    mss = st.get("mss")
    if mss == ("mss_up" if cand.direction == "up" else "mss_down"):
        pts += 4
    sweeps = st.get("recent_sweeps") or []
    want = "below" if cand.direction == "up" else "above"
    if any(s.get("side") == want for s in sweeps):
        pts += 2
    return _clamp(pts, CAPS["structure"]), notes


_IMPORTANT = {"pdh": 1.0, "pdl": 1.0, "pwh": 1.0, "pwl": 1.0, "or_high": 0.8, "or_low": 0.8, "session_high": 0.7,
              "session_low": 0.7, "swing_high": 0.7, "swing_low": 0.7, "vwap": 0.6, "pdc": 0.5, "ema20": 0.5,
              "ema50": 0.6, "ema200": 0.8}


def _key_location(ctx: ContextSnapshot, cand: Candidate) -> tuple[int, list[str]]:
    behind = ctx.nearest("below" if cand.direction == "up" else "above")
    if not behind or behind.get("distance_atr") is None:
        return 0, []
    d = abs(behind["distance_atr"])
    proximity = max(0.0, 1.0 - d / 1.0)  # full marks at the level, nothing beyond 1 ATR
    importance = _IMPORTANT.get(behind["name"], 0.5)
    touches = min(int(behind.get("touches") or 0), 3) / 3.0
    pts = CAPS["key_location"] * (0.6 * proximity * importance + 0.25 * importance + 0.15 * touches)
    return _clamp(pts, CAPS["key_location"]), [f"at {behind['name']} ({d:.2f} ATR)"]


def _mtf(ctx: ContextSnapshot, cand: Candidate) -> tuple[int, list[str]]:
    a = ctx.alignment or {}
    label, pref = a.get("label"), a.get("direction_preference")
    base = {"STRONG_TREND_ALIGNMENT": 1.0, "TREND_ALIGNMENT": 0.75, "WEAK_ALIGNMENT": 0.45, "NEUTRAL": 0.2,
            "TREND_TRANSITION": 0.25, "COUNTER_TREND": 0.0}.get(label, 0.0)
    if pref not in (cand.direction, "none"):
        base = 0.0
    want = _BULL if cand.direction == "up" else _BEAR
    agree = sum(1 for tf in ("1d", "30m", "5m", "1m") if ctx.trend(tf).get("label") in want)
    pts = CAPS["mtf_alignment"] * (0.7 * base + 0.3 * agree / 4.0)
    return _clamp(pts, CAPS["mtf_alignment"]), [f"{label} pref={pref} agree={agree}/4"]


def _volume(ctx: ContextSnapshot, cand: Candidate) -> tuple[int, list[str]]:
    rv = ctx.volume.get("relative_volume")
    if rv is None:
        return 0, ["no volume data"]
    pts = CAPS["volume"] * max(0.0, min(1.0, (rv - 0.8) / 1.2))  # 0 at 0.8x, full at 2.0x
    slope = ctx.volume.get("obv_slope")
    if slope is not None and (slope > 0) == (cand.direction == "up"):
        pts = min(CAPS["volume"], pts + 2)
    return _clamp(pts, CAPS["volume"]), [f"rel_vol={rv:.2f}"]


def _momentum(ctx: ContextSnapshot, cand: Candidate) -> tuple[int, list[str]]:
    ind = ctx.indicators
    pts = 0.0
    h, r, adx = ind.get("macd_hist"), ind.get("rsi"), ind.get("adx")
    if h is not None and (h > 0) == (cand.direction == "up"):
        pts += 4
    if r is not None:
        edge = (r - 50) / 25.0 if cand.direction == "up" else (50 - r) / 25.0
        pts += 3 * max(0.0, min(1.0, edge))
        if (cand.direction == "up" and r > 75) or (cand.direction == "down" and r < 25):
            pts -= 3  # exhausted, not strong
    if adx is not None:
        pts += 3 * max(0.0, min(1.0, (adx - 15) / 20.0))
    return _clamp(pts, CAPS["momentum"]), []


def _volatility(ctx: ContextSnapshot, cand: Candidate) -> tuple[int, list[str]]:
    p = ctx.indicators.get("atr_percentile")
    if p is None:
        return 2, []
    # sweet spot: normal-to-elevated; both dead and extreme volatility score low
    pts = CAPS["volatility"] * (1.0 - abs(p - 55.0) / 55.0)
    return _clamp(pts, CAPS["volatility"]), [f"atr_pct={p:.0f}"]


def _liquidity_execution(x: ExternalInputs) -> tuple[int, list[str]]:
    if x.spread_pct is None:
        return 0, []
    pts = CAPS["liquidity_execution"] * max(0.0, 1.0 - x.spread_pct / x.max_spread_pct)
    if x.open_interest is not None and x.open_interest < x.min_open_interest:
        pts *= 0.5
    return _clamp(pts, CAPS["liquidity_execution"]), [f"spread={x.spread_pct:.2f}%"]


def _option_quality(x: ExternalInputs) -> tuple[int, list[str]]:
    if x.theta_pct_of_premium is None:
        return 0, []
    pts = CAPS["option_quality"] * max(0.0, 1.0 - x.theta_pct_of_premium / x.max_theta_pct)
    return _clamp(pts, CAPS["option_quality"]), [f"theta={x.theta_pct_of_premium:.1%}/day"]


def _penalties(ctx: ContextSnapshot, cand: Candidate, x: ExternalInputs, comps: dict[str, int]) -> dict[str, int]:
    pen: dict[str, int] = {}
    want = _BULL if cand.direction == "up" else _BEAR
    against = _BEAR if cand.direction == "up" else _BULL
    labels_30_5 = [ctx.trend(tf).get("label") for tf in ("30m", "5m")]
    if any(l in against for l in labels_30_5) and any(l in want for l in labels_30_5):
        pen["conflicting_timeframes"] = PENALTIES["conflicting_timeframes"]
    if cand.counter_trend:
        pen["counter_trend"] = PENALTIES["counter_trend"]
    if comps["structure"] < 4:
        pen["weak_structure"] = PENALTIES["weak_structure"]
    rv = ctx.volume.get("relative_volume")
    if rv is not None and rv < 0.7:
        pen["weak_volume"] = PENALTIES["weak_volume"]
    if comps["key_location"] < 4:
        pen["poor_location"] = PENALTIES["poor_location"]
    ahead = ctx.nearest("above" if cand.direction == "up" else "below")
    if ahead and ahead.get("distance_atr") is not None and abs(ahead["distance_atr"]) < 0.5 and ahead["price"] != cand.target_ref:
        pen["nearby_opposing_level"] = PENALTIES["nearby_opposing_level"]
    if x.spread_pct is not None and x.spread_pct > x.max_spread_pct:
        pen["excessive_spread"] = PENALTIES["excessive_spread"]
    if x.open_interest is not None and x.open_interest < x.min_open_interest:
        pen["poor_liquidity"] = PENALTIES["poor_liquidity"]
    if x.remaining_move_atr is not None and x.remaining_move_atr < x.min_remaining_move_atr:
        pen["insufficient_expected_movement"] = PENALTIES["insufficient_expected_movement"]
    if x.theta_pct_of_premium is not None and x.theta_pct_of_premium > x.max_theta_pct:
        pen["excessive_theta"] = PENALTIES["excessive_theta"]
    if x.move_consumed_atr is not None and x.move_consumed_atr > 2.0:
        pen["extension"] = PENALTIES["extension"]
    behind = ctx.nearest("below" if cand.direction == "up" else "above")
    if behind and behind.get("distance_atr") is not None and abs(behind["distance_atr"]) > 2.0:
        pen.setdefault("extension", PENALTIES["extension"])
    regime = ctx.regime.get("primary")
    if regime in ("CHOPPY", "LOW_VOLATILITY") or (regime in ("STRONG_BEAR", "BEAR") and cand.direction == "up") or \
            (regime in ("STRONG_BULL", "BULL") and cand.direction == "down"):
        pen["regime_mismatch"] = PENALTIES["regime_mismatch"]
    return pen


def score(ctx: ContextSnapshot, cand: Candidate, external: ExternalInputs | None = None) -> Score:
    x = external or ExternalInputs()
    comps: dict[str, int] = {}
    notes: list[str] = []
    for name, fn in (("price_action", _price_action), ("structure", _structure), ("key_location", _key_location),
                     ("mtf_alignment", _mtf), ("volume", _volume), ("momentum", _momentum), ("volatility", _volatility)):
        pts, n = fn(ctx, cand)
        comps[name] = pts
        notes.extend(n)
    for name, fn in (("liquidity_execution", _liquidity_execution), ("option_quality", _option_quality)):
        pts, n = fn(x)
        comps[name] = pts
        notes.extend(n)
    pen = _penalties(ctx, cand, x, comps)
    total = max(0, min(100, sum(comps.values()) - sum(pen.values())))
    return Score(total=total, components=comps, penalties=pen, notes=notes)


# --- opportunity ranking (§15, §35) ---------------------------------------------------------

CORRELATED = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})  # §35: the three indices are one exposure bucket


@dataclass
class Ranked:
    candidate: Candidate
    score: Score
    rank: int
    selected: bool
    reason: str


def rank(scored: list[tuple[Candidate, Score]], *, open_directions: dict[str, str] | None = None,
         max_selected: int = 1, min_score: int = 0) -> list[Ranked]:
    """Order by score (strategy name breaks ties deterministically, then
    reward/risk), then select at most `max_selected` with the §35 rule: no
    second position in the correlated index bucket in the same direction,
    whether already open (`open_directions`: underlying -> direction) or
    selected earlier in this pass."""
    order = sorted(scored, key=lambda cs: (-cs[1].total, cs[0].strategy, -cs[0].reward_distance / max(cs[0].risk_distance, 1e-9)))
    taken_dirs = set((open_directions or {}).values())
    out: list[Ranked] = []
    n_sel = 0
    for i, (cand, sc) in enumerate(order, start=1):
        if sc.total < min_score:
            out.append(Ranked(cand, sc, i, False, "below_min_score"))
            continue
        if n_sel >= max_selected:
            out.append(Ranked(cand, sc, i, False, "max_selected_reached"))
            continue
        if cand.underlying in CORRELATED and cand.direction in taken_dirs:
            out.append(Ranked(cand, sc, i, False, "correlated_exposure"))
            continue
        out.append(Ranked(cand, sc, i, True, "selected"))
        n_sel += 1
        if cand.underlying in CORRELATED:
            taken_dirs.add(cand.direction)
    return out
