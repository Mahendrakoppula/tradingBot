"""Fingerprint engine (spec §63): one pipe-delimited key per setup so
results can be aggregated by Strategy x Regime x Trend x Index x
Timeframes x Time-of-day x CE/PE x location x trigger x volume.

    NIFTY|TREND_PULLBACK|BULL|DAILY+30M+5M|SUPPORT|SWEEP|BOS|VWAP_RECLAIM|HIGH_VOLUME|CE|MORNING

Every component is a closed vocabulary (no free text, no numbers) so the
same market situation always produces the same key.
"""
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.strategies.base import Candidate

_TF_NAMES = {"1d": "DAILY", "30m": "30M", "5m": "5M", "1m": "1M"}
_BULL = ("STRONG_BULL", "BULL", "WEAK_BULL")
_BEAR = ("STRONG_BEAR", "BEAR", "WEAK_BEAR")
# §37 time-of-day phases (clock.SESSION_PHASES labels) -> fingerprint words
_SESSION_TOD = {
    "PRE_MARKET": "PRE", "09:15-10:00": "OPEN", "10:00-11:30": "MORNING", "11:30-13:00": "MIDDAY",
    "13:00-14:00": "EARLY_AFTERNOON", "14:00-15:00": "AFTERNOON", "15:00-15:30": "CLOSE", "POST_MARKET": "POST",
}


def _trend_word(label: str | None) -> str:
    if label in _BULL:
        return "BULL"
    if label in _BEAR:
        return "BEAR"
    return (label or "NEUTRAL").upper()


def _agreeing_tfs(ctx: ContextSnapshot, direction: str) -> str:
    want = _BULL if direction == "up" else _BEAR
    tfs = [_TF_NAMES[tf] for tf in ("1d", "30m", "5m", "1m") if ctx.trend(tf).get("label") in want]
    return "+".join(tfs) if tfs else "NONE"


def _location(ctx: ContextSnapshot, cand: Candidate) -> str:
    behind = ctx.nearest("below" if cand.direction == "up" else "above")
    if not behind or behind.get("distance_atr") is None or abs(behind["distance_atr"]) > 0.75:
        return "OPEN"
    name = behind["name"]
    if name in ("pdh", "pdl", "pwh", "pwl", "pdc"):
        return "PDLEVEL"
    if name in ("ema20", "ema50", "ema200"):
        return "EMA"
    if name == "vwap":
        return "VWAP"
    if name in ("or_high", "or_low"):
        return "OPENING_RANGE"
    if name in ("session_high", "session_low"):
        return "SESSION_EXTREME"
    return "SUPPORT" if cand.direction == "up" else "RESISTANCE"


def _liquidity(ctx: ContextSnapshot) -> str:
    return "SWEEP" if (ctx.price_action.get("sweep") or ctx.structure.get("recent_sweeps")) else "NO_SWEEP"


def _structure(ctx: ContextSnapshot) -> str:
    ev = (ctx.structure.get("last_event") or {}).get("kind", "")
    if ctx.structure.get("mss"):
        return "MSS"
    if ev.startswith("bos"):
        return "BOS"
    if ev.startswith("choch"):
        return "CHOCH"
    return "NO_BREAK"


def _trigger(cand: Candidate) -> str:
    c = cand.confirmation.upper()
    for key in ("VWAP", "DISPLACEMENT", "REJECTION", "RETEST", "OR_BREAK", "TRAP", "SWEEP", "BOS", "CHOCH", "PULLBACK", "PRICE_ACTION"):
        if key in c:
            return key
    return "OTHER"


def _volume(ctx: ContextSnapshot) -> str:
    rv = ctx.volume.get("relative_volume")
    if rv is None:
        return "NO_VOLUME"
    if rv >= 1.5:
        return "HIGH_VOLUME"
    if rv >= 1.0:
        return "NORMAL_VOLUME"
    return "LOW_VOLUME"


def fingerprint(ctx: ContextSnapshot, cand: Candidate) -> str:
    parts = [
        ctx.underlying.upper(),
        cand.strategy,
        _trend_word(ctx.trend("5m").get("label")),
        _agreeing_tfs(ctx, cand.direction),
        _location(ctx, cand),
        _liquidity(ctx),
        _structure(ctx),
        _trigger(cand),
        _volume(ctx),
        cand.option_type,
        _SESSION_TOD.get(ctx.session_phase, "OTHER"),
    ]
    return "|".join(parts)
