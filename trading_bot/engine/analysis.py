"""Full recompute for one underlying at a trigger-bar close: candle stores
in, ContextSnapshot out (spec §38 slow path). Pure - no I/O, no clock reads
- so the same function serves live SHADOW and BACKTEST replay (§51).

Everything is computed on CLOSED bars only (`candles[:i+1]` where i is the
last closed bar); higher timeframes are read as of their own last close,
never a bar that is still forming (mirrors mtf.aligned_view's rule).
"""
import dataclasses
import datetime as dt
from dataclasses import dataclass, field

from trading_bot import indicators as ind
from trading_bot.engine.clock import session_open_at, session_phase
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.price_action import anatomy, detect_patterns
from trading_bot.engine.regime import MarketRegime, RegimeConfig, RegimeInputs, classify_market_regime
from trading_bot.engine.structure import (
    day_levels,
    detect_mss,
    find_structure_events,
    liquidity_sweep,
    nearest_levels,
    session_levels,
    visible_swings,
)
from trading_bot.engine.trend import TFTrend, TrendConfig, align, classify_tf_trend

TF_ORDER = ("1d", "30m", "5m", "1m")  # highest first - lower TFs read `higher`
RECENT_LABELS_KEEP = 10
PERCENTILE_LOOKBACK = 100
VWAP_SLOPE_BARS = 30  # 1m bars: VWAP drift over the last half hour

EMA_CROSS_LOOKBACK = 20
TOUCH_TOLERANCE_ATR = 0.2
RECENT_SWEEPS_KEEP = 6
RECENT_REGIMES_KEEP = 8


@dataclass
class AnalysisState:
    """Carried across bars per underlying: what the engines need from their
    own previous read (persistence, hysteresis, flip counting, touches)."""
    prev_trends: dict[str, TFTrend] = field(default_factory=dict)
    recent_labels: dict[str, list[str]] = field(default_factory=dict)
    prev_regime: MarketRegime | None = None
    level_touches: dict[str, int] = field(default_factory=dict)
    touch_day: dt.date | None = None
    # short histories the strategy families read (spec §14): recent sweeps
    # with the bar they happened on, recent regime primaries, previous VWAP
    recent_sweeps: list[dict] = field(default_factory=list)
    recent_regimes: list[str] = field(default_factory=list)
    prev_vwap: float | None = None


@dataclass(frozen=True)
class EngineParams:
    """The subset of EngineConfig the analysis reads, plus the engine configs
    derived from it - so tests can build one without env vars."""
    align_weights: dict[str, float]
    trend: TrendConfig = TrendConfig()
    regime: RegimeConfig = RegimeConfig()
    volume_proxy: str = "futures"

    @classmethod
    def from_config(cls, cfg) -> "EngineParams":
        return cls(
            align_weights=cfg.align_weights,
            trend=TrendConfig(adx_min=cfg.trend_adx_min, adx_strong=cfg.trend_adx_strong),
            regime=RegimeConfig(atr_pct_high=cfg.regime_atr_pct_high, atr_pct_low=cfg.regime_atr_pct_low,
                                bb_width_pct_compression=cfg.bb_compression_pct, adx_trend_min=cfg.trend_adx_min),
            volume_proxy=cfg.volume_proxy,
        )


def _last(series, i=None):
    if not series:
        return None
    return series[-1] if i is None else series[i]


def _sign_changes(values: list[float | None]) -> int:
    prev, n = None, 0
    for v in values:
        if v is None or v == 0:
            continue
        s = v > 0
        if prev is not None and s != prev:
            n += 1
        prev = s
    return n


def build_context(
    underlying: str,
    candles: dict[str, list[dict]],
    *,
    now: dt.datetime,
    quality: str,
    state: AnalysisState,
    params: EngineParams,
    volume_candles: dict[str, list[dict]] | None = None,
    trigger_tf: str = "5m",
    bar_index: int | None = None,
) -> ContextSnapshot:
    """`candles[tf]` are CLOSED bars (dict shape) for the spot instrument;
    `volume_candles[tf]` the futures proxy's, same shape, or None. `now` is
    the trigger bar's close instant (IST)."""
    c5 = candles.get(trigger_tf) or []
    if not c5:
        raise ValueError("no trigger-tf candles")
    i = len(c5) - 1
    bar = c5[i]
    spot = float(bar["close"])
    closes = [c["close"] for c in c5]
    today = now.date()

    # --- indicators on the trigger tf ------------------------------------------------
    atr_s = ind.atr(c5, 14)
    atr_v = _last(atr_s)
    ema20, ema50, ema200 = (_last(ind.ema(closes, p)) for p in (20, 50, 200))
    rsi_v = _last(ind.rsi(closes, 14))
    _, _, hist = ind.macd(closes)
    adx_s, _, _ = ind.adx(c5, 14)
    _, _, _, bbw = ind.bollinger(closes, 20)
    bbw_pct = _last(ind.percentile_rank_series(bbw, PERCENTILE_LOOKBACK))
    atr_pct_series = [(a / c * 100.0) if (a is not None and c) else None for a, c in zip(atr_s, closes)]
    atr_pct = _last(ind.percentile_rank_series(atr_pct_series, PERCENTILE_LOOKBACK))
    ema20_s = ind.ema(closes, 20)
    cross_count = _sign_changes([(c - e) if e is not None else None for c, e in zip(closes, ema20_s)][-EMA_CROSS_LOOKBACK:])

    # --- volume (proxy) --------------------------------------------------------------------
    vol: dict = {"volume_proxy": "none", "relative_volume": None, "obv_slope": None}
    vwap_v = None
    vwap_slope = None
    vc = (volume_candles or {}).get(trigger_tf) or []
    if vc:
        vol["volume_proxy"] = params.volume_proxy
        vol["relative_volume"] = _last(ind.relative_volume_series(vc, 20))
        obv_s = ind.obv(vc)
        sl = ind.slope(obv_s, 10)
        vol["obv_slope"] = _last(sl)
        v1m = (volume_candles or {}).get("1m") or []
        today_v1m = [c for c in v1m if c["ts"].date() == today]
        vw_series = ind.session_vwap(today_v1m) if today_v1m else []
        vw = _last(vw_series)
        if vw is not None:
            # futures trade at a basis to spot - shift the proxy VWAP by the
            # current basis so it sits on the spot price scale
            basis = float(vc[-1]["close"]) - spot
            vwap_v = vw - basis
            vol["vwap_basis"] = basis
            # VWAP bias: where price sits relative to the day's money (ATR units) and which way
            # the VWAP itself is drifting over the last VWAP_SLOPE_BARS minutes. Read by scoring
            # (volume component / against-VWAP penalty) and journaled for segmentation.
            past = [v for v in vw_series[:-VWAP_SLOPE_BARS] if v is not None]
            vwap_slope = (vw - past[-1]) if past and len(vw_series) > VWAP_SLOPE_BARS else None

    # --- trends per tf + alignment -------------------------------------------------------------
    trends: dict[str, TFTrend] = {}
    higher: TFTrend | None = None
    for tf in TF_ORDER:
        ctf = candles.get(tf) or []
        if not ctf:
            continue
        recent = state.recent_labels.setdefault(tf, [])
        t = classify_tf_trend(ctf, tf, params.trend, prev=state.prev_trends.get(tf), higher=higher,
                              recent_labels=recent, vwap_value=vwap_v if tf != "1d" else None)
        trends[tf] = t
        higher = t
    alignment = align(trends, params.align_weights)

    # --- structure & levels ----------------------------------------------------------------------
    swings = visible_swings(c5, i)
    events = find_structure_events(swings)
    last_event = events[-1] if events else None
    mss = detect_mss(events, c5, i, atr_v)
    last_hi = next((s.price for s in reversed(swings) if s.kind == "high"), None)
    last_lo = next((s.price for s in reversed(swings) if s.kind == "low"), None)
    in_swing_range = last_hi is not None and last_lo is not None and last_lo <= spot <= last_hi

    levels: list[tuple[str, float]] = []
    dl = day_levels(candles.get("1d") or [])
    if dl:
        levels += [("pdh", dl.pdh), ("pdl", dl.pdl), ("pdc", dl.pdc)]
        if dl.pwh is not None:
            levels += [("pwh", dl.pwh), ("pwl", dl.pwl)]
    today_1m = [c for c in (candles.get("1m") or []) if c["ts"].date() == today]
    sl_ = session_levels(today_1m) if today_1m else None
    if sl_:
        levels += [("session_high", sl_.session_high), ("session_low", sl_.session_low)]
        if sl_.or_complete and sl_.or_high is not None:
            levels += [("or_high", sl_.or_high), ("or_low", sl_.or_low)]
    if last_hi is not None:
        levels.append(("swing_high", last_hi))
    if last_lo is not None:
        levels.append(("swing_low", last_lo))
    for name, val in (("ema20", ema20), ("ema50", ema50), ("ema200", ema200)):
        if val is not None:
            levels.append((name, val))
    if vwap_v is not None:
        levels.append(("vwap", vwap_v))

    # touch counting (per day): a bar whose high/low comes within tolerance of a level
    if state.touch_day != today:
        state.level_touches, state.touch_day = {}, today
    if atr_v:
        for name, lvl in levels:
            if abs(bar["high"] - lvl) <= TOUCH_TOLERANCE_ATR * atr_v or abs(bar["low"] - lvl) <= TOUCH_TOLERANCE_ATR * atr_v:
                state.level_touches[name] = state.level_touches.get(name, 0) + 1

    report = nearest_levels(spot, levels, atr_v)

    def _ld(d):
        if d is None:
            return None
        return {"name": d.name, "price": d.price, "distance": d.distance, "distance_atr": d.distance_atr,
                "touches": state.level_touches.get(d.name, 0)}

    level_map = {name: lvl for name, lvl in levels}
    level_map["nearest_above"] = _ld(report.nearest_above)
    level_map["nearest_below"] = _ld(report.nearest_below)

    # --- price action -------------------------------------------------------------------------------
    labels = detect_patterns(c5, i, atr_v)
    sweep = liquidity_sweep(bar, [(n, l) for n, l in levels if n not in ("ema20", "ema50", "ema200")], atr_v)
    pa = {"labels": labels, "anatomy": dataclasses.asdict(anatomy(bar, atr_v)),
          "sweep": dataclasses.asdict(sweep) if sweep else None}
    if sweep:
        state.recent_sweeps.append({**dataclasses.asdict(sweep), "bar_index": i, "wick": bar["high"] if sweep.side == "above" else bar["low"]})
        del state.recent_sweeps[:-RECENT_SWEEPS_KEEP]
    # drop sweeps from a previous day's bar numbering when the store rolls
    recent_sweeps = [sw for sw in state.recent_sweeps if sw["bar_index"] <= i]

    # --- regime ---------------------------------------------------------------------------------------
    t5 = trends.get(trigger_tf)
    minutes_since_open = int((now - session_open_at(today)).total_seconds() // 60)
    x = RegimeInputs(
        quality_ok=(quality == "OK"), minutes_since_open=minutes_since_open,
        trend_score=t5.score if t5 else 0.0, trend_label=t5.label if t5 else "NEUTRAL",
        trend_transition=bool(t5 and t5.transition), trend_unstable=bool(t5 and t5.label == "UNSTABLE"),
        adx=_last(adx_s), atr_percentile=atr_pct, bb_width_percentile=bbw_pct, ema_fast_cross_count=cross_count,
        close=spot, prev_close=float(c5[i - 1]["close"]) if i > 0 else spot,
        bar_range_atr=((bar["high"] - bar["low"]) / atr_v) if atr_v else None,
        opening_range=(sl_.or_high, sl_.or_low) if (sl_ and sl_.or_complete and sl_.or_high is not None) else None,
        pdh=dl.pdh if dl else None, pdl=dl.pdl if dl else None, in_swing_range=in_swing_range,
    )
    regime = classify_market_regime(x, state.prev_regime, params.regime)

    # --- carry state ------------------------------------------------------------------------------------
    for tf, t in trends.items():
        state.prev_trends[tf] = t
        rl = state.recent_labels.setdefault(tf, [])
        rl.append(t.label)
        del rl[:-RECENT_LABELS_KEEP]
    state.prev_regime = regime
    state.recent_regimes.append(regime.primary)
    del state.recent_regimes[:-RECENT_REGIMES_KEEP]
    prev_vwap, state.prev_vwap = state.prev_vwap, vwap_v

    return ContextSnapshot(
        ts=now, underlying=underlying, trigger_tf=trigger_tf, spot=spot, session_phase=session_phase(now.time()),
        quality=quality, bar_index=i if bar_index is None else bar_index,
        trends={tf: dataclasses.asdict(t) for tf, t in trends.items()},
        alignment=dataclasses.asdict(alignment),
        regime={**dataclasses.asdict(regime), "recent_primaries": list(state.recent_regimes)},
        structure={
            "last_event": dataclasses.asdict(last_event) if last_event else None,
            "events_count": len(events), "mss": mss, "swing_high": last_hi, "swing_low": last_lo,
            "in_swing_range": in_swing_range, "recent_sweeps": recent_sweeps,
        },
        levels=level_map,
        price_action=pa,
        indicators={
            "rsi": rsi_v, "macd_hist": _last(hist), "adx": _last(adx_s), "atr": atr_v,
            "atr_pct": _last(atr_pct_series), "atr_percentile": atr_pct, "bb_width_pct": _last(bbw),
            "bb_width_percentile": bbw_pct, "ema20": ema20, "ema50": ema50, "ema200": ema200, "vwap": vwap_v,
            "vwap_prev": prev_vwap, "close_prev": float(c5[i - 1]["close"]) if i > 0 else None,
            "vwap_distance_atr": round((spot - vwap_v) / atr_v, 3) if (vwap_v is not None and atr_v) else None,
            "vwap_slope_atr": round(vwap_slope / atr_v, 3) if (vwap_slope is not None and atr_v) else None,
            "ema20_cross_count": cross_count,
        },
        volume=vol,
    )
