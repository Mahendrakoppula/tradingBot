"""Explainability (spec §68): every would-be signal and every rejection is
rendered as the SAME 20-key mandatory template, so a human (or the nightly
review agent) can answer "why this, why now, why not" from one record.

Keys that need M2 engines (option, strike, expiry, risk, EV) are filled
with the literal "not_evaluated_in_M1" instead of being omitted - the
template shape is the contract and stays stable across milestones.
"""
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.presignal import StageEvent

NOT_EVALUATED = "not_evaluated_in_M1"

EXPLANATION_KEYS: tuple[str, ...] = (
    "Direction", "Strategy", "Trend", "Regime", "Location", "Structure", "Price Action",
    "Confirmation", "Volume", "Momentum", "MTF", "Expected Move", "Remaining Move",
    "Option", "Strike", "Expiry", "Risk", "Expected Net Reward", "Expected Value", "Decision",
)


def _fmt(v, nd=2):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _trend_line(ctx: ContextSnapshot) -> str:
    parts = []
    for tf in ("1d", "30m", "5m", "1m"):
        t = ctx.trend(tf)
        if t:
            parts.append(f"{tf}={t.get('label')}({_fmt(t.get('score'))})")
    return ", ".join(parts) or "n/a"


def _location_line(ctx: ContextSnapshot, direction: str) -> str:
    near = ctx.nearest("above" if direction == "up" else "below")
    if not near:
        return "no reference level within reach"
    return (f"{near.get('name')} @ {_fmt(near.get('price'))}, "
            f"{_fmt(near.get('distance_atr'))} ATR away, touches={near.get('touches', 0)}")


def _structure_line(ctx: ContextSnapshot) -> str:
    st = ctx.structure
    last = st.get("last_event") or {}
    bits = []
    if last:
        bits.append(f"last {last.get('kind')} @ {_fmt(last.get('price'))} (bar {last.get('index')})")
    if st.get("mss"):
        bits.append(f"MSS {st['mss'].get('direction') if isinstance(st['mss'], dict) else st['mss']}")
    if st.get("failed_breakout"):
        bits.append("failed breakout")
    if ctx.price_action.get("sweep"):
        sw = ctx.price_action["sweep"]
        bits.append(f"sweep of {sw.get('level_name')} ({sw.get('direction')})" if isinstance(sw, dict) else "sweep")
    return "; ".join(bits) or "no recent structure event"


def _momentum_line(ctx: ContextSnapshot) -> str:
    ind = ctx.indicators
    return f"RSI={_fmt(ind.get('rsi'), 1)}, MACD_hist={_fmt(ind.get('macd_hist'), 3)}, ADX={_fmt(ind.get('adx'), 1)}"


def _volume_line(ctx: ContextSnapshot) -> str:
    v = ctx.volume
    proxy = v.get("volume_proxy", "none")
    if proxy == "none":
        return "no volume (index tick volume is zero, no proxy configured)"
    return f"rel_vol={_fmt(v.get('relative_volume'))} via {proxy} proxy, OBV_slope={_fmt(v.get('obv_slope'), 3)}"


def _confirmation_line(event: StageEvent) -> str:
    d = event.details
    if event.to_stage == "TRADE_READY":
        return (f"confirmation bar closed beyond trigger {_fmt(d.get('trigger_level'))} "
                f"(extension {_fmt(d.get('extension_atr'))} ATR); reason={event.reason_code}")
    if event.to_stage == "CONFIRMING":
        return f"trigger candidate: {event.reason_code} at {_fmt(d.get('level'))}"
    return f"{event.from_stage}->{event.to_stage}: {event.reason_code}"


def _decision_line(event: StageEvent) -> str:
    if event.to_stage == "TRADE_READY":
        return "WOULD-BE SIGNAL (SHADOW: logged only, no order)"
    if event.to_stage in ("EXPIRED", "REJECTED", "EXTENDED", "EXHAUSTED"):
        return f"NO TRADE - {event.to_stage.lower()} ({event.reason_code})"
    return f"WATCHING - {event.to_stage.lower()}"


def build_explanation(ctx: ContextSnapshot, event: StageEvent) -> dict:
    """Return the §68 template as an ordered dict of str values. Always
    contains exactly EXPLANATION_KEYS, in that order."""
    direction = event.direction
    reg = ctx.regime
    out = {
        "Direction": f"{direction} ({'CE' if direction == 'up' else 'PE'} bias)",
        "Strategy": f"family_hint={event.details.get('family_hint') or ctx.structure.get('family_hint') or 'unclassified'} (strategy engines are M2)",
        "Trend": _trend_line(ctx),
        "Regime": f"{reg.get('primary', 'n/a')}" + (f" (transition {reg['transition']})" if reg.get("transition") else ""),
        "Location": _location_line(ctx, direction),
        "Structure": _structure_line(ctx),
        "Price Action": ", ".join(ctx.price_action.get("labels", [])) or "no pattern at trigger bar",
        "Confirmation": _confirmation_line(event),
        "Volume": _volume_line(ctx),
        "Momentum": _momentum_line(ctx),
        "MTF": f"{ctx.alignment.get('label', 'n/a')} (score {_fmt(ctx.alignment.get('weighted_score'))}, pref {ctx.alignment.get('direction_preference', 'none')})",
        "Expected Move": NOT_EVALUATED,
        "Remaining Move": NOT_EVALUATED,
        "Option": NOT_EVALUATED,
        "Strike": NOT_EVALUATED,
        "Expiry": NOT_EVALUATED,
        "Risk": NOT_EVALUATED,
        "Expected Net Reward": NOT_EVALUATED,
        "Expected Value": NOT_EVALUATED,
        "Decision": _decision_line(event),
    }
    assert tuple(out) == EXPLANATION_KEYS
    return out


def render(explanation: dict) -> str:
    """Plain-text block for Telegram / journald (HTML-escaping is the
    notifier's job)."""
    return "\n".join(f"{k}: {v}" for k, v in explanation.items())
