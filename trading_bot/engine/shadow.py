"""The M1 event loop (spec §38 slow path, §50-51 modes): ticks -> bars ->
context -> pre-signal -> journal. Identical code path for live SHADOW and
BACKTEST replay - only the TickSource and the clock differ (§51 parity).

Hard rule: this module, and everything it imports, has NO way to reach an
order endpoint (tests/test_engine_no_orders.py enforces it by AST scan).
A TRADE_READY setup becomes a `signals` row with status='would_be' and a
Telegram note; nothing else happens.

M1 simplification, deliberate: every engine runs at the 5m close only
(trigger_tf). The plan sketched a 1m-driven CONFIRMING->TRADE_READY leg;
mixing cadences would make pre-signal confidence decay depend on which bar
fired, so M1 keeps one cadence and M2 revisits once strategies exist.
"""
import datetime as dt
import logging
import platform
import uuid
from dataclasses import dataclass, field
from typing import Callable, Protocol

from trading_bot.engine import jsonlog
from trading_bot.engine.analysis import AnalysisState, EngineParams, build_context
from trading_bot.engine.candles import BarAggregator, Candle, CandleStore
from trading_bot.engine.clock import TF_MINUTES, session_close_at
from trading_bot.engine.eod import eod_summary
from trading_bot.engine.explain import build_explanation, render
from trading_bot.engine.presignal import PreSignalConfig, PreSignalTracker, StageEvent
from trading_bot.engine.quality import FeedHealth, assess
from trading_bot.engine.warmup import Instrument

log = logging.getLogger(__name__)

AGG_TFS = ("1m", "5m", "30m", "1d")
TRIGGER_TF = "5m"
# bars handed to the analysis per tf: enough for EMA200/percentile(100)
# warmup on every tf, bounded so a 5m recompute stays well under a second
MAX_BARS = {"1m": 800, "5m": 800, "30m": 500, "1d": 400}
EOD_GRACE = dt.timedelta(seconds=5)


class TickSource(Protocol):
    def start(self) -> None: ...
    def next(self, timeout: float):
        """Next tick or None after `timeout` seconds (live) / immediately when exhausted (replay)."""
    def health(self) -> FeedHealth: ...
    def done(self) -> bool: ...
    def close(self) -> None: ...


class Notifier(Protocol):
    def notify(self, message: str, *, html: bool = False) -> None: ...


@dataclass
class LoopStats:
    ticks: int = 0
    bars_closed: int = 0
    snapshots: int = 0
    stage_events: int = 0
    would_be_signals: int = 0
    alerts_sent: int = 0
    alerts_suppressed: int = 0
    quality: dict = field(default_factory=dict)


class ShadowLoop:
    def __init__(
        self,
        cfg,
        dal,
        instruments: list[Instrument],
        source: TickSource,
        clock: Callable[[], dt.datetime],
        notifier: Notifier | None,
        stores: dict[tuple[str, str], CandleStore],
        *,
        params: EngineParams | None = None,
        presignal_cfg: PreSignalConfig | None = None,
        run_id: uuid.UUID | None = None,
        git_sha: str | None = None,
        after_feed_start: Callable[[], None] | None = None,
    ):
        self.cfg = cfg
        self.dal = dal
        self.instruments = instruments
        self.source = source
        self.clock = clock
        self.notifier = notifier
        self.stores = stores
        self.params = params or EngineParams.from_config(cfg)
        self.presignal = PreSignalTracker(presignal_cfg or PreSignalConfig(
            ttl_bars=cfg.presignal_ttl_bars, decay=cfg.presignal_decay, min_conf=cfg.presignal_min_conf,
            level_proximity_atr=cfg.level_proximity_atr))
        self.run_id = run_id or uuid.uuid4()
        self.git_sha = git_sha
        self.after_feed_start = after_feed_start  # live: today's 1m REST backfill once the WS is up
        self.mode = cfg.mode
        self.stats = LoopStats()
        self.states: dict[str, AnalysisState] = {}
        self.by_token: dict[str, Instrument] = {i.token: i for i in instruments}
        self.spot_of: dict[str, Instrument] = {i.underlying: i for i in instruments if i.role == "spot"}
        self.proxy_of: dict[str, Instrument] = {i.underlying: i for i in instruments if i.role == "volume_proxy"}
        self.aggs: dict[str, BarAggregator] = {
            i.token: BarAggregator(i.token, AGG_TFS, on_close=lambda tf, c, tok=i.token: None, source="ws")
            for i in instruments
        }
        self._alert_times: list[dt.datetime] = []
        self._stopped = False
        self.config_version_id: int | None = None
        self.day: dt.date | None = None

    # --- lifecycle -----------------------------------------------------------------

    def stop(self) -> None:
        self._stopped = True

    def start(self) -> None:
        now = self.clock()
        self.day = now.date()
        payload = {k: (v if isinstance(v, (int, float, str, bool, list, tuple)) else str(v)) for k, v in vars(self.cfg).items()}
        self.config_version_id = self.dal.get_or_create_config_version(payload)
        self.dal.insert_run(self.run_id, self.mode, now, git_sha=self.git_sha, host=platform.node(),
                            config_version_id=self.config_version_id,
                            notes={"instruments": [vars(i) for i in self.instruments]})
        jsonlog.event("loop", "start", run_id=str(self.run_id), mode=self.mode, day=self.day.isoformat(),
                      instruments=[i.token for i in self.instruments], live_orders=False)
        self.source.start()
        if self.after_feed_start is not None:
            self.after_feed_start()

    def run(self) -> LoopStats:
        self.start()
        try:
            while not self._stopped:
                tick = self.source.next(1.0)
                now = self.clock()
                if tick is not None:
                    self._on_tick(tick, now)
                self._flush(now)
                # replay: `done()` turns True as the last tick is consumed, but the
                # clock only jumps past the close on the NEXT (empty) pull - wait
                # for that so the final bars close through timers, not flush_all
                if (tick is None and self.source.done()) or now >= session_close_at(self.day) + EOD_GRACE:
                    break
            self._eod(stopped=self._stopped)
        except Exception as exc:  # noqa: BLE001 - record the failure, then re-raise
            jsonlog.event("loop", "crash", severity="ERROR", error=repr(exc))
            self.dal.end_run(self.run_id, self.clock(), status="crashed", notes={"error": repr(exc)})
            raise
        finally:
            self.source.close()
        return self.stats

    # --- ticks and bars -------------------------------------------------------------

    def _on_tick(self, tick, now: dt.datetime) -> None:
        agg = self.aggs.get(str(tick.token))
        if agg is None:
            return
        self.stats.ticks += 1
        closed = agg.on_tick(tick.ltp, tick.exchange_timestamp, cum_volume=tick.volume,
                             oi=getattr(tick, "open_interest", None), now=now)
        for tf, bar in closed:
            self._on_bar(agg.token, tf, bar, now)

    def _flush(self, now: dt.datetime) -> None:
        for token, agg in self.aggs.items():
            for tf, bar in agg.flush_timers(now):
                self._on_bar(token, tf, bar, now)

    def _on_bar(self, token: str, tf: str, bar: Candle, now: dt.datetime) -> None:
        inst = self.by_token[token]
        store = self.stores.setdefault((token, tf), CandleStore(tf))
        store.upsert(bar)
        self.dal.upsert_candles(token, inst.exchange, inst.underlying, tf, [bar], run_id=self.run_id)
        self.stats.bars_closed += 1
        if inst.role == "spot" and tf == TRIGGER_TF:
            self._on_trigger(inst, bar)

    # --- the slow path ------------------------------------------------------------------

    def _slices(self, token: str) -> dict[str, list[dict]]:
        out = {}
        for tf in AGG_TFS:
            st = self.stores.get((token, tf))
            if st is not None and len(st):
                out[tf] = st.dicts()[-MAX_BARS[tf]:]
        return out

    def _on_trigger(self, inst: Instrument, bar: Candle) -> None:
        u = inst.underlying
        close_at = bar.ts + dt.timedelta(minutes=TF_MINUTES[TRIGGER_TF])
        store_1m = self.stores.setdefault((inst.token, "1m"), CandleStore("1m"))
        q = assess(store_1m, self.source.health(), self.clock(), stale_tick_seconds=self.cfg.stale_tick_seconds,
                   clock_drift_seconds=self.cfg.clock_drift_seconds)
        self.stats.quality[q.status] = self.stats.quality.get(q.status, 0) + 1
        candles = self._slices(inst.token)
        proxy = self.proxy_of.get(u)
        vcandles = self._slices(proxy.token) if proxy is not None else None
        state = self.states.setdefault(u, AnalysisState())
        bar_index = len(candles[TRIGGER_TF]) - 1
        try:
            ctx = build_context(u, candles, now=close_at, quality=q.status, state=state, params=self.params,
                                volume_candles=vcandles, bar_index=bar_index)
        except Exception as exc:  # noqa: BLE001 - one bad bar must not kill the session
            jsonlog.event("analysis", "error", severity="ERROR", underlying=u, error=repr(exc), bar_ts=bar.ts)
            log.exception("build_context failed for %s at %s", u, bar.ts)
            return
        ctx_id = self.dal.insert_context_snapshot(self.run_id, ctx, self.config_version_id)
        self.stats.snapshots += 1
        jsonlog.event("context", "snapshot", underlying=u, ts=close_at, context_snapshot_id=ctx_id, spot=ctx.spot,
                      regime=ctx.regime.get("primary"), alignment=ctx.alignment.get("label"),
                      trend_5m=ctx.trend("5m").get("label"), quality=q.status, quality_reasons=list(q.reasons))
        if q.status != "OK":
            jsonlog.event("quality", "degraded", severity="WARN", underlying=u, ts=close_at, status=q.status,
                          reasons=list(q.reasons))
        for ev in self.presignal.update(ctx):
            self._on_stage_event(ctx, ev, ctx_id)

    def _on_stage_event(self, ctx, ev: StageEvent, ctx_id: int) -> None:
        self.stats.stage_events += 1
        self.dal.insert_presignal_event(self.run_id, ctx.ts, ev, context_snapshot_id=ctx_id)
        decision = "would_be_signal" if ev.to_stage == "TRADE_READY" else ev.to_stage.lower()
        jsonlog.event("presignal", "stage_change", underlying=ev.underlying, setup_id=ev.setup_id, ts=ctx.ts,
                      decision=decision, reason_code=ev.reason_code, from_stage=ev.from_stage, to_stage=ev.to_stage,
                      direction=ev.direction, confidence=ev.confidence, details=ev.details)
        if ev.to_stage != "TRADE_READY":
            return
        explanation = build_explanation(ctx, ev)
        signal_id = uuid.uuid5(uuid.NAMESPACE_URL, f"signal/{ev.setup_id}/{ctx.ts.isoformat()}")  # deterministic for replay parity
        snapshot = {"ts": ctx.ts, "spot": ctx.spot, "direction": ev.direction, "trigger_level": ev.details.get("trigger_level"),
                    "atr": ctx.atr, "regime": ctx.regime.get("primary"), "alignment": ctx.alignment.get("label"),
                    "option": None, "strike": None, "expiry": None, "quantity": None}
        self.dal.insert_signal(self.run_id, signal_id=signal_id, setup_id=ev.setup_id, ts=ctx.ts, mode=self.mode,
                               underlying=ev.underlying, direction=ev.direction, stage=ev.to_stage, status="would_be",
                               explanation=explanation, snapshot=snapshot, reason_code=ev.reason_code,
                               context_snapshot_id=ctx_id, option_type="CE" if ev.direction == "up" else "PE")
        self.stats.would_be_signals += 1
        jsonlog.event("signal", "would_be", underlying=ev.underlying, setup_id=ev.setup_id, signal_id=str(signal_id),
                      ts=ctx.ts, decision="would_be_signal", reason_code=ev.reason_code, explanation=explanation)
        self._alert(f"WOULD-BE SIGNAL ({self.mode}, no order)\n{ev.underlying} {ev.direction.upper()} @ {ctx.spot:.2f} "
                    f"{ctx.ts:%H:%M}\n\n{render(explanation)}", ctx.ts)

    # --- notifications ---------------------------------------------------------------------

    def _alert(self, text: str, now: dt.datetime) -> None:
        if self.notifier is None:
            return
        cutoff = now - dt.timedelta(hours=1)
        self._alert_times = [t for t in self._alert_times if t > cutoff]
        if len(self._alert_times) >= self.cfg.telegram_max_alerts_per_hour:
            self.stats.alerts_suppressed += 1
            return
        try:
            self.notifier.notify(text)
            self._alert_times.append(now)
            self.stats.alerts_sent += 1
        except Exception as exc:  # noqa: BLE001 - never let Telegram take the loop down
            log.warning("notify failed: %s", exc)

    # --- end of day -----------------------------------------------------------------------------

    def _eod(self, stopped: bool = False) -> None:
        """Session close, or an external stop (SIGTERM from a deploy/instance
        stop). Both flush partial bars and end the run, but a stop is
        recorded and reported as STOPPED so nobody mistakes a 12:01 redeploy
        for the day's summary."""
        now = self.clock()
        partial = 0
        for token, agg in self.aggs.items():
            inst = self.by_token[token]
            for tf, bar in agg.flush_all():
                self.dal.upsert_candles(token, inst.exchange, inst.underlying, tf, [bar], run_id=self.run_id)
                partial += 1
        h = self.source.health()
        feed = {"ticks": self.stats.ticks, "reconnects": h.reconnects, "partial_bars": partial,
                "late_ticks": sum(a.late_ticks for a in self.aggs.values()),
                "quality": self.stats.quality}
        summary = eod_summary(self.dal, self.run_id, self.day, self.mode, feed,
                              heading="STOPPED" if stopped else "EOD")
        status = "stopped" if stopped else "completed"
        self.dal.end_run(self.run_id, now, status=status, notes={"stats": vars(self.stats), "feed": feed})
        jsonlog.event("loop", "stopped" if stopped else "eod", run_id=str(self.run_id), stats=vars(self.stats), feed=feed)
        for line in summary.splitlines():
            log.info(line)
        if self.notifier is not None:
            try:
                self.notifier.notify(summary)
            except Exception as exc:  # noqa: BLE001
                log.warning("EOD notify failed: %s", exc)
