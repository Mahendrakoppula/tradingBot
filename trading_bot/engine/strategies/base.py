"""Strategy engine contracts (spec §14). A strategy is a pure evaluator:
ContextSnapshot + the pre-signal state in -> Candidate or NoTrade out.

Every Candidate carries the four things §14 makes mandatory and the rest
of the pipeline builds on: an objective entry reference, an objective
confirmation, a market-driven invalidation level (the structural SL on
the UNDERLYING, §26 - never derived from a desired R:R) and a market-
driven target reference (§28). Option selection, sizing and execution
come later and never here: "strategy code must never place broker orders
directly" (§14, §92 #35) - the AST guard in tests/test_engine_no_orders.py
covers this package.

These families are research hypotheses (§14: "Do NOT assume
profitability"). Thresholds are first-cut and live in StrategyParams so
M3 can move them without code changes.
"""
from dataclasses import dataclass, field
from typing import Protocol

from trading_bot.engine.context import ContextSnapshot

# Regime primaries a family is COMPATIBLE with (§15 "regime compatibility").
TREND_REGIMES = frozenset({"STRONG_BULL", "BULL", "WEAK_BULL", "STRONG_BEAR", "BEAR", "WEAK_BEAR"})
BREAK_REGIMES = frozenset({"BREAKOUT", "BREAKDOWN", "EXPANSION"})
RANGE_REGIMES = frozenset({"RANGE", "LOW_VOLATILITY"})
NEVER_TRADE_REGIMES = frozenset({"NO_TRADE", "UNSTABLE"})

ALIGNED = frozenset({"STRONG_TREND_ALIGNMENT", "TREND_ALIGNMENT"})
WEAKLY_ALIGNED = frozenset({"STRONG_TREND_ALIGNMENT", "TREND_ALIGNMENT", "WEAK_ALIGNMENT"})


DEFAULT_VETO_TFS: tuple[str, ...] = ("30m",)


@dataclass(frozen=True)
class StrategyParams:
    level_proximity_atr: float = 0.5  # "at" a level
    retest_proximity_atr: float = 0.35
    sl_buffer_atr: float = 0.25  # placed beyond the invalidating structure
    sweep_lookback_bars: int = 3
    structure_lookback_bars: int = 3
    compression_lookback_bars: int = 6
    orb_end_phase: str = "MORNING_TREND"  # ORB only until this session phase ends (§37 phases)
    orb_min_rel_volume: float = 1.2
    momentum_min_rel_volume: float = 1.5
    momentum_min_adx: float = 20.0
    default_target_atr: float = 1.5  # when no level is within reach
    counter_trend_veto_tfs: tuple[str, ...] = DEFAULT_VETO_TFS  # which HTFs make a trade counter-trend (§7)


@dataclass(frozen=True)
class StrategySpec:
    name: str
    version: str
    tier: int
    compatible_regimes: frozenset
    compatible_alignments: frozenset  # alignment labels the family may trade WITH the trend
    counter_trend_ok: bool  # may take a direction against the alignment preference (§7)
    family_hints: frozenset  # pre-signal family_hint values this family answers to (routing prefilter)


@dataclass
class Candidate:
    strategy: str
    version: str
    underlying: str
    direction: str  # "up" | "down"
    entry_ref: float  # underlying price the thesis is priced from (trigger-bar close)
    invalidation: float  # structural SL on the underlying (§26)
    target_ref: float  # first market-driven target on the underlying (§28)
    confirmation: str  # what objectively confirmed
    counter_trend: bool
    reasons: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    @property
    def option_type(self) -> str:
        return "CE" if self.direction == "up" else "PE"

    @property
    def risk_distance(self) -> float:
        return abs(self.entry_ref - self.invalidation)

    @property
    def reward_distance(self) -> float:
        return abs(self.target_ref - self.entry_ref)


@dataclass(frozen=True)
class NoTrade:
    strategy: str
    reason_code: str
    detail: str = ""


class Strategy(Protocol):
    spec: StrategySpec

    def evaluate(self, ctx: ContextSnapshot, direction: str, params: StrategyParams) -> Candidate | NoTrade: ...


# --- helpers shared by the families ---------------------------------------------------

def sign(direction: str) -> int:
    return 1 if direction == "up" else -1


def opposite(direction: str) -> str:
    return "down" if direction == "up" else "up"


def nearest_toward(ctx: ContextSnapshot, direction: str) -> dict | None:
    """The nearest level in the trade direction (the first thing that can
    block the move, §23) - the natural first target reference."""
    return ctx.nearest("above" if direction == "up" else "below")


def nearest_behind(ctx: ContextSnapshot, direction: str) -> dict | None:
    """The nearest level behind price - where the setup is 'located'."""
    return ctx.nearest("below" if direction == "up" else "above")


def default_target(ctx: ContextSnapshot, direction: str, params: StrategyParams, min_atr: float = 0.5) -> float:
    """Nearest level in the direction if it is at least `min_atr` away,
    else an ATR projection - a target must never sit on top of the entry."""
    atr = ctx.atr or 0.0
    lvl = nearest_toward(ctx, direction)
    if lvl and lvl.get("distance_atr") is not None and abs(lvl["distance_atr"]) >= min_atr:
        return float(lvl["price"])
    return ctx.spot + sign(direction) * params.default_target_atr * atr


def bullish_pa(labels: list[str]) -> bool:
    return bool({"bullish_pin", "bullish_engulfing", "rejection_of_low", "morning_star"} & set(labels))


def bearish_pa(labels: list[str]) -> bool:
    return bool({"bearish_pin", "bearish_engulfing", "rejection_of_high", "evening_star"} & set(labels))


def pa_with(direction: str, labels: list[str]) -> bool:
    return bullish_pa(labels) if direction == "up" else bearish_pa(labels)


def structure_break_with(ctx: ContextSnapshot, direction: str, lookback: int) -> dict | None:
    ev = (ctx.structure or {}).get("last_event") or {}
    want = ("bos_up", "choch_up") if direction == "up" else ("bos_down", "choch_down")
    if ev.get("kind") in want and ctx.bar_index - int(ev.get("index", -10**6)) <= lookback:
        return ev
    return None


def trend_with(ctx: ContextSnapshot, direction: str, tf: str = "5m") -> bool:
    t = ctx.trend(tf)
    return t.get("label", "") in (("STRONG_BULL", "BULL", "WEAK_BULL") if direction == "up" else ("STRONG_BEAR", "BEAR", "WEAK_BEAR"))


def alignment_with(ctx: ContextSnapshot, direction: str, labels: frozenset = WEAKLY_ALIGNED) -> bool:
    a = ctx.alignment or {}
    return a.get("label") in labels and a.get("direction_preference") == direction


def is_counter_trend(ctx: ContextSnapshot, direction: str, veto_tfs: tuple[str, ...] = DEFAULT_VETO_TFS,
                     min_score: float = 0.5) -> bool:
    """A trade is counter-trend when it goes AGAINST a decisive higher-
    timeframe read (spec §7, §92 #9): any timeframe in `veto_tfs` whose score
    opposes the trade with |score| >= min_score.

    Which timeframes may veto is config (TECH_COUNTER_TREND_VETO_TFS). Default
    "30m": for an intraday option buyer the 30m defines the day's trade; the
    daily defines bias and keeps its say through scoring (MTF component,
    regime penalty) rather than as a gate. First paper day (2026-09-21): with
    the daily strongly bearish and the 30m rising all day, a daily veto made
    every long counter-trend and nothing traded. An empty tuple makes the 5m
    read sovereign (only the families' own alignment rules remain) - the
    operator's call, recorded in config_versions like any other setting.

    The alignment LABEL alone is never used: align() reports COUNTER_TREND
    whenever any lower timeframe disagrees, which describes conflict, not
    direction.
    """
    for tf in veto_tfs:
        sc = ctx.trend(tf).get("score")
        if sc is not None and abs(sc) >= min_score and (sc > 0) != (direction == "up"):
            return True
    return False
