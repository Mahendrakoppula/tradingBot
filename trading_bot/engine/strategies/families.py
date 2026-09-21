"""The ten §14 strategy families. Each is a small object with a spec and an
`evaluate(ctx, direction, params)` that returns a Candidate (objective
entry, confirmation, invalidation, target) or a NoTrade with a reason code
the rejected-signal dataset (§72) can aggregate on.

Conventions: `direction` is the side the pre-signal tracker was developing
("up" -> CE, "down" -> PE). A family that trades REVERSALS (sweep, trap,
range extreme) still receives the direction of the intended TRADE - the
tracker already flipped it when it saw the rejection. Levels/ATR come from
the ContextSnapshot only; nothing here reads candles or the clock.
"""
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.strategies.base import (
    ALIGNED,
    BREAK_REGIMES,
    RANGE_REGIMES,
    TREND_REGIMES,
    WEAKLY_ALIGNED,
    Candidate,
    NoTrade,
    StrategyParams,
    StrategySpec,
    alignment_with,
    default_target,
    nearest_behind,
    nearest_toward,
    pa_with,
    sign,
    structure_break_with,
    trend_with,
)

VERSION = "0.1"  # first-cut family definitions; bump on any rule change (§92 #44)


def _cand(spec: StrategySpec, ctx: ContextSnapshot, direction: str, invalidation: float, target: float,
          confirmation: str, reasons: list[str], **evidence) -> Candidate:
    return Candidate(
        strategy=spec.name, version=spec.version, underlying=ctx.underlying, direction=direction,
        entry_ref=ctx.spot, invalidation=round(invalidation, 2), target_ref=round(target, 2),
        confirmation=confirmation, counter_trend=False, reasons=reasons, evidence=evidence,  # set by route() with the configured veto
    )


def _sl_beyond(level: float, direction: str, atr: float, buffer_atr: float) -> float:
    return level - sign(direction) * buffer_atr * atr


# --- Tier 1 -------------------------------------------------------------------------------


class LiquiditySweepBOS:
    spec = StrategySpec("LIQUIDITY_SWEEP_BOS", VERSION, 1,
                        TREND_REGIMES | RANGE_REGIMES | BREAK_REGIMES | {"TRANSITION", "HIGH_VOLATILITY", "NORMAL"},
                        WEAKLY_ALIGNED | {"NEUTRAL", "TREND_TRANSITION"}, counter_trend_ok=True,
                        family_hints=frozenset({"liquidity_sweep", "unclassified"}))

    def evaluate(self, ctx, direction, params):
        atr = ctx.atr or 0.0
        want_side = "below" if direction == "up" else "above"  # a long needs a swept LOW
        sweeps = [s for s in (ctx.structure.get("recent_sweeps") or [])
                  if s.get("side") == want_side and ctx.bar_index - s["bar_index"] <= params.sweep_lookback_bars]
        if not sweeps:
            return NoTrade(self.spec.name, "no_recent_sweep")
        brk = structure_break_with(ctx, direction, params.structure_lookback_bars)
        if brk is None:
            return NoTrade(self.spec.name, "no_structure_break_after_sweep")
        sw = sweeps[-1]
        if brk.get("index", -1) < sw["bar_index"]:
            return NoTrade(self.spec.name, "structure_break_precedes_sweep")
        sl = _sl_beyond(sw["wick"], direction, atr, params.sl_buffer_atr)
        return _cand(self.spec, ctx, direction, sl, default_target(ctx, direction, params),
                     f"sweep_of_{sw['level_name']}_then_{brk['kind']}",
                     [f"swept {sw['level_name']} @ {sw['level']:.2f}", f"{brk['kind']} @ {brk['price']:.2f}"],
                     sweep=sw, structure_break=brk)


class CompressionBreakoutRetest:
    spec = StrategySpec("COMPRESSION_BREAKOUT", VERSION, 1, BREAK_REGIMES | {"COMPRESSION", "TRANSITION"} | TREND_REGIMES,
                        WEAKLY_ALIGNED | {"NEUTRAL", "TREND_TRANSITION"}, counter_trend_ok=False,
                        family_hints=frozenset({"compression_breakout"}))

    def evaluate(self, ctx, direction, params):
        recent = ctx.regime.get("recent_primaries") or []
        if "COMPRESSION" not in recent[-params.compression_lookback_bars:]:
            return NoTrade(self.spec.name, "no_recent_compression")
        atr = ctx.atr or 0.0
        behind = nearest_behind(ctx, direction)  # the level just broken, now behind price
        if behind is None or behind.get("distance_atr") is None:
            return NoTrade(self.spec.name, "no_breakout_level")
        if abs(behind["distance_atr"]) > 1.0:
            return NoTrade(self.spec.name, "extended_from_breakout_level", f"{abs(behind['distance_atr']):.2f} ATR")
        labels = ctx.price_action.get("labels", [])
        retested = abs(behind["distance_atr"]) <= params.retest_proximity_atr
        confirmed = "displacement" in labels or pa_with(direction, labels) or retested
        if not confirmed:
            return NoTrade(self.spec.name, "no_breakout_confirmation")
        sl = _sl_beyond(behind["price"], direction, atr, params.sl_buffer_atr + 0.25)
        return _cand(self.spec, ctx, direction, sl, default_target(ctx, direction, params),
                     "retest_hold" if retested else "displacement_break",
                     [f"compression in last {params.compression_lookback_bars} bars", f"broke {behind['name']} @ {behind['price']:.2f}"],
                     breakout_level=behind, retested=retested)


class TrendPullbackContinuation:
    spec = StrategySpec("TREND_PULLBACK", VERSION, 1, TREND_REGIMES | {"NORMAL", "EXPANSION"}, ALIGNED,
                        counter_trend_ok=False, family_hints=frozenset({"trend_pullback", "vwap_reclaim", "unclassified"}))
    PULLBACK_LEVELS = ("ema20", "ema50", "vwap", "swing_low", "swing_high", "session_low", "session_high", "or_high", "or_low")

    def evaluate(self, ctx, direction, params):
        if not alignment_with(ctx, direction, ALIGNED):
            return NoTrade(self.spec.name, "not_aligned")
        if not trend_with(ctx, direction, "5m") and not trend_with(ctx, direction, "30m"):
            return NoTrade(self.spec.name, "no_trend_on_5m_or_30m")
        behind = nearest_behind(ctx, direction)
        if behind is None or behind["name"] not in self.PULLBACK_LEVELS or behind.get("distance_atr") is None:
            return NoTrade(self.spec.name, "no_pullback_level")
        if abs(behind["distance_atr"]) > params.level_proximity_atr:
            return NoTrade(self.spec.name, "not_at_pullback_level", f"{abs(behind['distance_atr']):.2f} ATR")
        labels = ctx.price_action.get("labels", [])
        if not (pa_with(direction, labels) or structure_break_with(ctx, direction, params.structure_lookback_bars)):
            return NoTrade(self.spec.name, "no_continuation_confirmation")
        atr = ctx.atr or 0.0
        sl = _sl_beyond(behind["price"], direction, atr, params.sl_buffer_atr)
        return _cand(self.spec, ctx, direction, sl, default_target(ctx, direction, params),
                     "pullback_rejection" if pa_with(direction, labels) else "pullback_structure_break",
                     [f"aligned {ctx.alignment.get('label')}", f"pullback to {behind['name']} @ {behind['price']:.2f}"],
                     pullback_level=behind)


class MTFConfluence:
    spec = StrategySpec("MTF_CONFLUENCE", VERSION, 1, TREND_REGIMES | BREAK_REGIMES, frozenset({"STRONG_TREND_ALIGNMENT"}),
                        counter_trend_ok=False, family_hints=frozenset({"trend_pullback", "compression_breakout", "unclassified"}))

    def evaluate(self, ctx, direction, params):
        if not alignment_with(ctx, direction, frozenset({"STRONG_TREND_ALIGNMENT"})):
            return NoTrade(self.spec.name, "alignment_not_strong")
        tfs_with = [tf for tf in ("1d", "30m", "5m", "1m") if trend_with(ctx, direction, tf)]
        if len(tfs_with) < 3:
            return NoTrade(self.spec.name, "fewer_than_three_tfs_agree", ",".join(tfs_with))
        brk = structure_break_with(ctx, direction, params.structure_lookback_bars)
        labels = ctx.price_action.get("labels", [])
        if brk is None and not pa_with(direction, labels):
            return NoTrade(self.spec.name, "no_trigger")
        behind = nearest_behind(ctx, direction)
        atr = ctx.atr or 0.0
        swing = ctx.structure.get("swing_low" if direction == "up" else "swing_high")
        anchor = swing if swing is not None else (behind["price"] if behind else ctx.spot - sign(direction) * atr)
        sl = _sl_beyond(anchor, direction, atr, params.sl_buffer_atr)
        return _cand(self.spec, ctx, direction, sl, default_target(ctx, direction, params),
                     brk["kind"] if brk else "price_action", [f"{len(tfs_with)} TFs agree: {','.join(tfs_with)}"],
                     tfs=tfs_with, structure_break=brk)


# --- Tier 2 -------------------------------------------------------------------------------


class OpeningRangeBreakout:
    spec = StrategySpec("ORB", VERSION, 2, BREAK_REGIMES | TREND_REGIMES | {"TRANSITION", "NORMAL"},
                        WEAKLY_ALIGNED | {"NEUTRAL", "TREND_TRANSITION"}, counter_trend_ok=False,
                        family_hints=frozenset({"compression_breakout", "unclassified"}))
    PHASES = ("09:15-10:00", "10:00-11:30")  # §37 phases; ORB is a morning setup

    def evaluate(self, ctx, direction, params):
        if ctx.session_phase not in self.PHASES:
            return NoTrade(self.spec.name, "outside_orb_window", ctx.session_phase)
        hi, lo = ctx.levels.get("or_high"), ctx.levels.get("or_low")
        if hi is None or lo is None:
            return NoTrade(self.spec.name, "opening_range_incomplete")
        beyond = ctx.spot > hi if direction == "up" else ctx.spot < lo
        if not beyond:
            return NoTrade(self.spec.name, "inside_opening_range")
        rv = ctx.volume.get("relative_volume")
        labels = ctx.price_action.get("labels", [])
        if "displacement" not in labels and (rv is None or rv < params.orb_min_rel_volume):
            return NoTrade(self.spec.name, "breakout_without_participation")
        rng = hi - lo
        level = hi if direction == "up" else lo
        atr = ctx.atr or 0.0
        if abs(ctx.spot - level) > 1.0 * atr:
            return NoTrade(self.spec.name, "extended_from_range", f"{abs(ctx.spot - level) / atr:.2f} ATR" if atr else "")
        sl = (hi + lo) / 2.0  # range midpoint: the breakout is wrong if price is back inside the middle
        target = level + sign(direction) * rng  # measured move, capped by the nearest level via default_target
        lvl = nearest_toward(ctx, direction)
        if lvl and (lvl["price"] - ctx.spot) * sign(direction) > 0 and abs(lvl["price"] - ctx.spot) < abs(target - ctx.spot):
            target = lvl["price"]
        return _cand(self.spec, ctx, direction, sl, target, "or_break_with_volume" if rv else "or_break_displacement",
                     [f"OR {lo:.2f}-{hi:.2f}", f"rel_vol={rv}"], or_high=hi, or_low=lo, rel_volume=rv)


class VWAPReclaim:
    spec = StrategySpec("VWAP_RECLAIM", VERSION, 2, TREND_REGIMES | {"TRANSITION", "NORMAL", "RANGE", "EXPANSION"},
                        WEAKLY_ALIGNED | {"NEUTRAL", "TREND_TRANSITION"}, counter_trend_ok=True,
                        family_hints=frozenset({"vwap_reclaim", "unclassified"}))

    def evaluate(self, ctx, direction, params):
        ind = ctx.indicators
        vwap, vprev, cprev = ind.get("vwap"), ind.get("vwap_prev"), ind.get("close_prev")
        if vwap is None or vprev is None or cprev is None:
            return NoTrade(self.spec.name, "no_vwap")
        crossed = (cprev <= vprev and ctx.spot > vwap) if direction == "up" else (cprev >= vprev and ctx.spot < vwap)
        if not crossed:
            return NoTrade(self.spec.name, "no_vwap_cross")
        labels = ctx.price_action.get("labels", [])
        brk = structure_break_with(ctx, direction, params.structure_lookback_bars)
        if brk is None and not pa_with(direction, labels) and "displacement" not in labels:
            return NoTrade(self.spec.name, "vwap_cross_without_structure")
        atr = ctx.atr or 0.0
        sl = _sl_beyond(vwap, direction, atr, params.sl_buffer_atr + 0.25)
        return _cand(self.spec, ctx, direction, sl, default_target(ctx, direction, params),
                     "vwap_reclaim" if direction == "up" else "vwap_loss",
                     [f"crossed VWAP {vwap:.2f}", brk["kind"] if brk else "price action"], vwap=vwap, structure_break=brk)


# --- Tier 3 -------------------------------------------------------------------------------


class PDHPDLTrap:
    spec = StrategySpec("PDH_PDL_TRAP", VERSION, 3, RANGE_REGIMES | TREND_REGIMES | {"TRANSITION", "NORMAL", "HIGH_VOLATILITY"},
                        WEAKLY_ALIGNED | {"NEUTRAL", "TREND_TRANSITION", "COUNTER_TREND"}, counter_trend_ok=True,
                        family_hints=frozenset({"liquidity_sweep", "unclassified"}))

    def evaluate(self, ctx, direction, params):
        want_side = "below" if direction == "up" else "above"
        sweeps = [s for s in (ctx.structure.get("recent_sweeps") or [])
                  if s.get("level_name") in ("pdh", "pdl") and s.get("side") == want_side
                  and ctx.bar_index - s["bar_index"] <= params.sweep_lookback_bars]
        if not sweeps:
            return NoTrade(self.spec.name, "no_pdh_pdl_sweep")
        sw = sweeps[-1]
        back_inside = ctx.spot > sw["level"] if direction == "up" else ctx.spot < sw["level"]
        if not back_inside:
            return NoTrade(self.spec.name, "not_back_inside_level")
        labels = ctx.price_action.get("labels", [])
        if not pa_with(direction, labels) and structure_break_with(ctx, direction, params.structure_lookback_bars) is None:
            return NoTrade(self.spec.name, "trap_without_confirmation")
        atr = ctx.atr or 0.0
        sl = _sl_beyond(sw["wick"], direction, atr, params.sl_buffer_atr)
        vwap = ctx.indicators.get("vwap")
        target = vwap if (vwap is not None and (vwap - ctx.spot) * sign(direction) > 0.5 * atr) else default_target(ctx, direction, params)
        return _cand(self.spec, ctx, direction, sl, target, f"{sw['level_name']}_trap_reversal",
                     [f"swept {sw['level_name']} @ {sw['level']:.2f} and closed back inside"], sweep=sw)


class EMAPullback:
    spec = StrategySpec("EMA_PULLBACK", VERSION, 3, TREND_REGIMES | {"NORMAL"}, WEAKLY_ALIGNED, counter_trend_ok=False,
                        family_hints=frozenset({"trend_pullback", "unclassified"}))

    def evaluate(self, ctx, direction, params):
        if not trend_with(ctx, direction, "5m") or not alignment_with(ctx, direction, WEAKLY_ALIGNED):
            return NoTrade(self.spec.name, "no_trend_or_alignment")
        behind = nearest_behind(ctx, direction)
        if behind is None or behind["name"] not in ("ema20", "ema50") or behind.get("distance_atr") is None:
            return NoTrade(self.spec.name, "not_at_ema")
        if abs(behind["distance_atr"]) > params.retest_proximity_atr:
            return NoTrade(self.spec.name, "ema_not_close_enough", f"{abs(behind['distance_atr']):.2f} ATR")
        labels = ctx.price_action.get("labels", [])
        if not pa_with(direction, labels):
            return NoTrade(self.spec.name, "no_rejection_at_ema")
        ema50 = ctx.indicators.get("ema50")
        atr = ctx.atr or 0.0
        anchor = min(behind["price"], ema50) if (direction == "up" and ema50) else max(behind["price"], ema50) if ema50 else behind["price"]
        sl = _sl_beyond(anchor, direction, atr, params.sl_buffer_atr)
        return _cand(self.spec, ctx, direction, sl, default_target(ctx, direction, params), f"rejection_at_{behind['name']}",
                     [f"{behind['name']} @ {behind['price']:.2f}"], ema_level=behind)


class MomentumExpansion:
    spec = StrategySpec("MOMENTUM_EXPANSION", VERSION, 3, BREAK_REGIMES | {"STRONG_BULL", "STRONG_BEAR", "BULL", "BEAR", "HIGH_VOLATILITY"},
                        WEAKLY_ALIGNED | {"NEUTRAL"}, counter_trend_ok=False,
                        family_hints=frozenset({"compression_breakout", "trend_pullback", "unclassified"}))

    def evaluate(self, ctx, direction, params):
        labels = ctx.price_action.get("labels", [])
        if "displacement" not in labels:
            return NoTrade(self.spec.name, "no_displacement")
        an = ctx.price_action.get("anatomy") or {}
        bullish_bar = bool(an.get("bullish", True))
        if bullish_bar != (direction == "up"):
            return NoTrade(self.spec.name, "displacement_against_direction")
        ind = ctx.indicators
        hist = ind.get("macd_hist")
        if hist is None or (hist > 0) != (direction == "up"):
            return NoTrade(self.spec.name, "momentum_against")
        if (ind.get("adx") or 0) < params.momentum_min_adx:
            return NoTrade(self.spec.name, "adx_too_low")
        rv = ctx.volume.get("relative_volume")
        if rv is not None and rv < params.momentum_min_rel_volume:
            return NoTrade(self.spec.name, "volume_not_expanding", f"rel_vol={rv:.2f}")
        atr = ctx.atr or 0.0
        # anatomy gives range + close location: reconstruct the bar's extremes from the close
        rng = float(an.get("range", atr))
        bar_low = ctx.spot - float(an.get("close_loc", 0.5)) * rng
        bar_high = bar_low + rng
        sl = _sl_beyond(bar_low if direction == "up" else bar_high, direction, atr, params.sl_buffer_atr)
        return _cand(self.spec, ctx, direction, sl, default_target(ctx, direction, params, min_atr=0.75), "displacement_with_momentum",
                     [f"displacement bar, macd_hist={hist:.3f}, adx={ind.get('adx'):.1f}", f"rel_vol={rv}"],
                     rel_volume=rv, bullish_bar=bullish_bar)


class RangeExtremeReversal:
    spec = StrategySpec("RANGE_EXTREME_REVERSAL", VERSION, 3, RANGE_REGIMES | {"CHOPPY"},
                        frozenset({"NEUTRAL", "WEAK_ALIGNMENT", "COUNTER_TREND", "TREND_TRANSITION"}) | WEAKLY_ALIGNED,
                        counter_trend_ok=True, family_hints=frozenset({"unclassified", "liquidity_sweep", "vwap_reclaim"}))
    EXTREMES = ("swing_high", "swing_low", "session_high", "session_low", "pdh", "pdl", "or_high", "or_low")

    def evaluate(self, ctx, direction, params):
        if ctx.regime.get("primary") not in self.spec.compatible_regimes:
            return NoTrade(self.spec.name, "not_a_range")
        # a long is taken at the range LOW (the level just behind price)
        behind = nearest_behind(ctx, direction)
        if behind is None or behind["name"] not in self.EXTREMES or behind.get("distance_atr") is None:
            return NoTrade(self.spec.name, "not_at_range_extreme")
        if abs(behind["distance_atr"]) > params.level_proximity_atr:
            return NoTrade(self.spec.name, "extreme_not_close_enough")
        labels = ctx.price_action.get("labels", [])
        if not pa_with(direction, labels):
            return NoTrade(self.spec.name, "no_rejection_at_extreme")
        atr = ctx.atr or 0.0
        sl = _sl_beyond(behind["price"], direction, atr, params.sl_buffer_atr + 0.1)
        hi, lo = ctx.structure.get("swing_high"), ctx.structure.get("swing_low")
        mid = (hi + lo) / 2.0 if (hi is not None and lo is not None) else None
        target = mid if (mid is not None and (mid - ctx.spot) * sign(direction) > 0.5 * atr) else default_target(ctx, direction, params)
        return _cand(self.spec, ctx, direction, sl, target, f"rejection_at_{behind['name']}",
                     [f"range regime {ctx.regime.get('primary')}", f"extreme {behind['name']} @ {behind['price']:.2f}"], extreme=behind)


FAMILIES: tuple = (
    LiquiditySweepBOS(), CompressionBreakoutRetest(), TrendPullbackContinuation(), MTFConfluence(),
    OpeningRangeBreakout(), VWAPReclaim(),
    PDHPDLTrap(), EMAPullback(), MomentumExpansion(), RangeExtremeReversal(),
)
