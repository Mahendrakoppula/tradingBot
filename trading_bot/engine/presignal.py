"""Pre-signal engine (spec §13): a per-(underlying, direction) state
machine that watches a setup DEVELOP before anything is allowed to call it
a signal.

    NO_SETUP -> EARLY_DEVELOPMENT -> PRE_SIGNAL -> CONFIRMING -> TRADE_READY
                                          \\-> EXPIRED / REJECTED / EXTENDED / EXHAUSTED

Critical rule (§13, §92 #34): PRE_SIGNAL NEVER PLACES ORDERS. This module
emits StageEvents - data - and has no execution dependency at all
(tests/test_engine_no_orders.py). In M1 a TRADE_READY event is logged as
a "would-be" signal with a full explanation; nothing acts on it.

Evidence is accumulated from a ContextSnapshot each bar; confidence decays
by `decay` on every bar that adds nothing new and the setup EXPIRES when
it falls under `min_conf` or exceeds `ttl_bars` without progress ("no
stale signal may be executed"). Counter-trend setups need materially more
evidence to reach TRADE_READY (§7). All thresholds are first-cut config.
"""
import uuid
from dataclasses import dataclass, field

from trading_bot.engine.context import ContextSnapshot

STAGES: tuple[str, ...] = (
    "NO_SETUP", "EARLY_DEVELOPMENT", "PRE_SIGNAL", "CONFIRMING", "TRADE_READY",
    "EXTENDED", "EXHAUSTED", "EXPIRED", "REJECTED",
)
TERMINAL_STAGES = frozenset({"EXTENDED", "EXHAUSTED", "EXPIRED", "REJECTED"})

# Evidence keys the tracker can collect. "level_approach" is the LOCATION
# evidence - PRE_SIGNAL requires it (a setup needs a where, not just a what).
# Compression / EMA squeeze / volume build-up say nothing about direction, so
# a setup only OPENS once at least one DIRECTIONAL key is present - otherwise
# every squeeze would spawn a mirror-image up AND down setup.
EVIDENCE_KEYS: tuple[str, ...] = (
    "level_approach", "compression", "ema_compression", "volume_buildup",
    "momentum_change", "vwap_interaction", "repeated_tests", "htf_alignment",
)
DIRECTIONAL_KEYS = frozenset({"level_approach", "momentum_change", "htf_alignment"})


@dataclass(frozen=True)
class PreSignalConfig:
    ttl_bars: int = 6
    decay: float = 0.85
    min_conf: float = 0.35
    level_proximity_atr: float = 0.5
    early_min_evidence: int = 2
    presignal_min_evidence: int = 4
    counter_trend_min_evidence: int = 6
    extended_atr: float = 2.0  # how far past the trigger level counts as chased
    ema_compression_atr: float = 0.5
    vwap_interaction_atr: float = 0.3
    volume_buildup_rel: float = 1.2
    repeated_tests_min: int = 2
    confidence_start: float = 0.30
    confidence_per_evidence: float = 0.10


@dataclass
class SetupState:
    setup_id: str
    underlying: str
    direction: str  # "up" | "down"
    stage: str
    confidence: float
    evidence: set = field(default_factory=set)
    opened_bar: int = 0
    last_progress_bar: int = 0
    trigger_level: float | None = None
    trigger_bar: int | None = None
    family_hint: str | None = None  # which §14 family this most resembles (M2 routes on it)


@dataclass(frozen=True)
class StageEvent:
    setup_id: str
    underlying: str
    direction: str
    from_stage: str
    to_stage: str
    confidence: float
    reason_code: str
    bar_index: int
    details: dict = field(default_factory=dict)


class PreSignalTracker:
    def __init__(self, cfg: PreSignalConfig):
        self.cfg = cfg
        self.setups: dict[tuple[str, str], SetupState] = {}

    # --- public --------------------------------------------------------------

    def update(self, ctx: ContextSnapshot) -> list[StageEvent]:
        """Advance both directions for ctx.underlying using this bar's
        context. Returns the stage transitions that happened (possibly
        none). Terminal setups are dropped so a fresh one can form."""
        events: list[StageEvent] = []
        for direction in ("up", "down"):
            key = (ctx.underlying, direction)
            state = self.setups.get(key)
            found = self._evidence(ctx, direction)
            if state is None:
                if len(found) >= self.cfg.early_min_evidence and found & DIRECTIONAL_KEYS:
                    # deterministic id (uuid5 of underlying/direction/bar time): a
                    # replay of the same candles must journal the same setup ids (§51)
                    setup_id = uuid.uuid5(uuid.NAMESPACE_URL, f"presignal/{ctx.underlying}/{direction}/{ctx.ts.isoformat()}")
                    state = SetupState(
                        setup_id=str(setup_id), underlying=ctx.underlying, direction=direction,
                        stage="NO_SETUP", evidence=set(found),
                        confidence=min(1.0, self.cfg.confidence_start + self.cfg.confidence_per_evidence * len(found)),
                        opened_bar=ctx.bar_index, last_progress_bar=ctx.bar_index,
                        family_hint=self._family_hint(found, ctx),
                    )
                    self.setups[key] = state
                    events.append(self._move(state, "EARLY_DEVELOPMENT", "evidence_threshold", ctx, {"evidence": sorted(found)}))
                continue
            events.extend(self._advance(state, found, ctx))
            if state.stage in TERMINAL_STAGES:
                del self.setups[key]
        return events

    def active(self, underlying: str | None = None) -> list[SetupState]:
        return [s for (u, _), s in self.setups.items() if underlying is None or u == underlying]

    # --- transitions -----------------------------------------------------------

    def _advance(self, s: SetupState, found: set, ctx: ContextSnapshot) -> list[StageEvent]:
        cfg = self.cfg
        events: list[StageEvent] = []
        new_evidence = found - s.evidence
        if new_evidence:
            s.evidence |= new_evidence
            s.confidence = min(1.0, s.confidence + cfg.confidence_per_evidence * len(new_evidence))
            s.last_progress_bar = ctx.bar_index
        else:
            s.confidence *= cfg.decay

        # invalidation / staleness first
        if ctx.trends.get("5m", {}).get("exhaustion") and s.stage in ("CONFIRMING", "TRADE_READY"):
            events.append(self._move(s, "EXHAUSTED", "trend_exhaustion", ctx))
            return events
        if s.confidence < cfg.min_conf:
            events.append(self._move(s, "EXPIRED", "confidence_decayed", ctx, {"confidence": round(s.confidence, 3)}))
            return events
        if ctx.bar_index - s.last_progress_bar >= cfg.ttl_bars:
            events.append(self._move(s, "EXPIRED", "ttl_exceeded", ctx, {"idle_bars": ctx.bar_index - s.last_progress_bar}))
            return events

        if s.stage == "EARLY_DEVELOPMENT":
            if len(s.evidence) >= cfg.presignal_min_evidence and "level_approach" in s.evidence:
                events.append(self._move(s, "PRE_SIGNAL", "evidence_and_location", ctx, {"evidence": sorted(s.evidence)}))
            return events

        if s.stage == "PRE_SIGNAL":
            trigger = self._trigger(ctx, s.direction)
            if trigger is not None:
                s.trigger_level, s.trigger_bar = trigger["level"], ctx.bar_index
                s.last_progress_bar = ctx.bar_index
                events.append(self._move(s, "CONFIRMING", trigger["kind"], ctx, trigger))
            return events

        if s.stage == "CONFIRMING":
            beyond, extension_atr = self._beyond_trigger(ctx, s)
            if not beyond:
                events.append(self._move(s, "REJECTED", "failed_trigger", ctx, {"trigger_level": s.trigger_level}))
                return events
            if extension_atr is not None and extension_atr >= cfg.extended_atr:
                events.append(self._move(s, "EXTENDED", "chased_past_trigger", ctx, {"extension_atr": round(extension_atr, 2)}))
                return events
            counter = ctx.alignment.get("label") == "COUNTER_TREND" or ctx.trend("5m").get("label") == "COUNTER_TREND"
            if counter and len(s.evidence) < cfg.counter_trend_min_evidence:
                # §7: a single lower-TF reversal must not override a strong HTF trend - wait
                return events
            s.last_progress_bar = ctx.bar_index
            events.append(self._move(s, "TRADE_READY", "confirmation_closed", ctx, {
                "trigger_level": s.trigger_level, "extension_atr": extension_atr,
                "counter_trend": counter, "evidence": sorted(s.evidence),
            }))
            return events

        if s.stage == "TRADE_READY":
            beyond, extension_atr = self._beyond_trigger(ctx, s)
            if not beyond:
                events.append(self._move(s, "REJECTED", "thesis_invalidated", ctx, {"trigger_level": s.trigger_level}))
            elif extension_atr is not None and extension_atr >= cfg.extended_atr:
                events.append(self._move(s, "EXTENDED", "chased_past_trigger", ctx, {"extension_atr": round(extension_atr, 2)}))
            return events
        return events

    def _move(self, s: SetupState, to: str, reason: str, ctx: ContextSnapshot, details: dict | None = None) -> StageEvent:
        ev = StageEvent(s.setup_id, s.underlying, s.direction, s.stage, to, round(s.confidence, 3), reason, ctx.bar_index, details or {})
        s.stage = to
        return ev

    # --- evidence -----------------------------------------------------------------

    def _evidence(self, ctx: ContextSnapshot, direction: str) -> set:
        cfg = self.cfg
        found: set = set()
        atr = ctx.atr or 0.0
        ind, vol = ctx.indicators, ctx.volume
        near = ctx.nearest("above" if direction == "up" else "below")
        if near and near.get("distance_atr") is not None and abs(near["distance_atr"]) <= cfg.level_proximity_atr:
            found.add("level_approach")
            if (near.get("touches") or 0) >= cfg.repeated_tests_min:
                found.add("repeated_tests")
        if ctx.regime.get("primary") == "COMPRESSION" or (ind.get("bb_width_pct") is not None and ind["bb_width_pct"] <= 20):
            found.add("compression")
        e20, e50 = ind.get("ema20"), ind.get("ema50")
        if e20 is not None and e50 is not None and atr > 0 and abs(e20 - e50) / atr <= cfg.ema_compression_atr:
            found.add("ema_compression")
        rv = vol.get("relative_volume")
        if rv is not None and rv >= cfg.volume_buildup_rel:
            found.add("volume_buildup")
        h = ind.get("macd_hist")
        r = ind.get("rsi")
        if (h is not None and ((h > 0) if direction == "up" else (h < 0))) or (
            r is not None and ((r > 50) if direction == "up" else (r < 50))
        ):
            found.add("momentum_change")
        vw = ind.get("vwap")
        if vw is not None and atr > 0 and abs(ctx.spot - vw) / atr <= cfg.vwap_interaction_atr:
            found.add("vwap_interaction")
        if ctx.alignment.get("direction_preference") == direction and ctx.alignment.get("label") in (
            "TREND_ALIGNMENT", "STRONG_TREND_ALIGNMENT"
        ):
            found.add("htf_alignment")
        return found

    def _trigger(self, ctx: ContextSnapshot, direction: str) -> dict | None:
        """A confirmation CANDIDATE at this bar: structure break, displacement
        beyond the level, or a rejection candle at the level - in the setup's
        direction. Returns {kind, level} or None."""
        st, pa = ctx.structure, ctx.price_action
        near = ctx.nearest("above" if direction == "up" else "below")
        level = near["price"] if near else None
        last = st.get("last_event") or {}
        if last.get("kind") in (("bos_up", "choch_up") if direction == "up" else ("bos_down", "choch_down")) and (
            ctx.bar_index - int(last.get("index", -99)) <= 2
        ):
            return {"kind": "structure_break", "level": level if level is not None else last.get("price"), "event": last.get("kind")}
        labels = set(pa.get("labels", []))
        if "displacement" in labels and level is not None:
            if (direction == "up" and ctx.spot > level) or (direction == "down" and ctx.spot < level):
                return {"kind": "displacement_break", "level": level}
        want = {"bullish_pin", "bullish_engulfing", "rejection_of_low", "morning_star"} if direction == "up" else {
            "bearish_pin", "bearish_engulfing", "rejection_of_high", "evening_star"
        }
        if labels & want and level is not None and near.get("distance_atr") is not None and abs(near["distance_atr"]) <= self.cfg.level_proximity_atr:
            return {"kind": "rejection_at_level", "level": level, "patterns": sorted(labels & want)}
        return None

    def _beyond_trigger(self, ctx: ContextSnapshot, s: SetupState) -> tuple[bool, float | None]:
        if s.trigger_level is None:
            return True, None
        atr = ctx.atr
        diff = (ctx.spot - s.trigger_level) if s.direction == "up" else (s.trigger_level - ctx.spot)
        beyond = diff >= 0
        return beyond, (diff / atr if atr else None)

    @staticmethod
    def _family_hint(found: set, ctx: ContextSnapshot) -> str:
        if "compression" in found:
            return "compression_breakout"
        if "vwap_interaction" in found:
            return "vwap_reclaim"
        if "level_approach" in found and "htf_alignment" in found:
            return "trend_pullback"
        if ctx.price_action.get("sweep"):
            return "liquidity_sweep"
        return "unclassified"
