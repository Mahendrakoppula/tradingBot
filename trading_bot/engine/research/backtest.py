"""Event-driven backtest (spec §73): stored 1m candles are replayed as
ticks through the SAME PaperLoop the live process runs, with the paper
broker's spread/slippage/latency/partials/rejects, over a range of days.

Option pricing: no historical option chain exists, so `ModelChainService`
synthesises a chain from the CURRENT spot with Black-Scholes at a
configured IV, a spread model and constant liquidity, and labels every
quote `greeks_source="model"`. It is deliberately conservative and
deliberately dumb - it cannot leak the future because it never sees it
(§84): each refresh reads only `spot` and `now`. Results from this engine
are for RELATIVE questions (does the pipeline behave, which regimes reject,
walk-forward stability) - never as proof of live profitability (§52/§83).

Account state carries across days (weekly caps, streaks); daily tallies
reset at each session.
"""
import datetime as dt
import uuid
from dataclasses import dataclass, field

from trading_bot.engine import bs
from trading_bot.engine.candles import Candle, CandleStore
from trading_bot.engine.clock import is_trading_day, session_open_at
from trading_bot.engine.db.memory import MemoryDAL
from trading_bot.engine.execution import PaperBroker, PaperParams
from trading_bot.engine.option_chain import CacheParams, ChainCache, OptionQuote
from trading_bot.engine.paper_loop import PaperLoop, session_elapsed
from trading_bot.engine.pipeline import PipelineParams
from trading_bot.engine.rejections import summarize
from trading_bot.engine.replay import SimClock, TickReplaySource
from trading_bot.engine.research.metrics import Metrics, compute
from trading_bot.engine.risk_engine import AccountState
from trading_bot.engine.warmup import Instrument
from trading_bot.options import OptionChain, OptionContract
from trading_bot.timeutil import IST

TFS = ("1m", "5m", "30m", "1d")

# first-cut contract conventions; configurable per run
DEFAULT_SPECS = {
    "NIFTY": {"exchange": "NFO", "step": 50.0, "lot": 75, "expiry_weekday": 1, "iv": 0.13},       # Tuesday
    "BANKNIFTY": {"exchange": "NFO", "step": 100.0, "lot": 35, "expiry_weekday": 2, "iv": 0.16},  # monthly in reality; weekly proxy
    "SENSEX": {"exchange": "BFO", "step": 100.0, "lot": 20, "expiry_weekday": 3, "iv": 0.13},     # Thursday
}


@dataclass(frozen=True)
class ModelChainParams:
    spread_pct: float = 1.0  # of mid, both sides
    min_spread: float = 0.10
    oi: int = 50_000
    volume: int = 20_000
    depth_qty: int = 500
    expiries: int = 2
    strikes_each_side: int = 6
    refresh_seconds: int = 60


class ModelChainService:
    """Drop-in for paper_loop.ChainService that prices from spot only."""

    def __init__(self, underlyings: dict[str, dict], today: dt.date, params: ModelChainParams | None = None):
        self.p = params or ModelChainParams()
        self.specs = underlyings
        self.today = today
        self.caches: dict[str, ChainCache] = {}
        self.errors = 0
        for u, spec in underlyings.items():
            self.caches[u] = ChainCache(u, OptionChain([], u, spec["exchange"]),
                                        CacheParams(strikes_each_side=self.p.strikes_each_side, expiries=self.p.expiries,
                                                    refresh_seconds=self.p.refresh_seconds))

    def _expiries(self, weekday: int) -> list[dt.date]:
        d = self.today
        out = []
        while len(out) < self.p.expiries:
            if d.weekday() == weekday:
                out.append(d)
            d += dt.timedelta(days=1)
        return out

    def due(self, u: str, now: dt.datetime) -> bool:
        c = self.caches.get(u)
        return c is not None and (c.last_refresh is None or (now - c.last_refresh).total_seconds() >= self.p.refresh_seconds)

    def refresh(self, u: str, spot: float, now: dt.datetime) -> ChainCache | None:
        c = self.caches.get(u)
        spec = self.specs.get(u)
        if c is None or spec is None or spot <= 0:
            return None
        step, lot, iv = spec["step"], spec["lot"], spec["iv"]
        atm = round(spot / step) * step
        elapsed = session_elapsed(now)
        quotes: dict[str, OptionQuote] = {}
        for exp in self._expiries(spec["expiry_weekday"]):
            dte = (exp - now.date()).days
            t = bs.years_to_expiry(dte, elapsed)
            for k in range(-self.p.strikes_each_side, self.p.strikes_each_side + 1):
                strike = atm + k * step
                for ot in ("CE", "PE"):
                    token = f"M{u[:2]}{exp:%y%m%d}{int(strike)}{ot}"
                    contract = OptionContract(token, f"{u}{exp:%d%b%y}".upper() + f"{int(strike)}{ot}", u, exp, strike, ot, lot, lot * 24, spec["exchange"])
                    g = bs.greeks(spot, strike, t, iv, ot)
                    mid = max(0.05, g.price)
                    half = max(self.p.min_spread, mid * self.p.spread_pct / 100.0) / 2.0
                    q = OptionQuote(contract, now, round(mid, 2), round(mid - half, 2), round(mid + half, 2), self.p.depth_qty,
                                    self.p.depth_qty, self.p.volume, self.p.oi, iv, g.delta, g.gamma, g.theta_per_day, g.vega,
                                    "model", spot)
                    quotes[token] = q
        c.quotes = quotes
        c.last_refresh = now
        return c


@dataclass
class DayResult:
    day: dt.date
    run_id: str
    snapshots: int
    decisions: int
    approved: int
    trades: int
    net_pnl: float
    rejections: dict


@dataclass
class BacktestReport:
    days: list[DayResult] = field(default_factory=list)
    trade_rows: list[dict] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    rejections: dict = field(default_factory=dict)
    metrics: Metrics = field(default_factory=Metrics)
    capital: float = 0.0
    profile: str = "realistic"

    def summary(self) -> str:
        from trading_bot.engine.research.metrics import render
        head = f"backtest {self.days[0].day if self.days else '?'} -> {self.days[-1].day if self.days else '?'} days={len(self.days)} " \
               f"decisions={sum(d.decisions for d in self.days)} approved={sum(d.approved for d in self.days)} profile={self.profile}\n"
        rej = "rejections: " + ", ".join(f"{k}={v}" for k, v in list(self.rejections.get("by_stage", {}).items())) + "\n"
        return head + rej + render(self.metrics)


class CandleSource:
    """Where the backtest gets bars: a DAL (load_candles) or an in-memory
    dict {token: {tf: [Candle]}}."""

    def __init__(self, dal=None, data: dict | None = None):
        self.dal = dal
        self.data = data or {}

    def load(self, token: str, tf: str, since: dt.datetime, until: dt.datetime) -> list[Candle]:
        if self.dal is not None:
            return self.dal.load_candles(token, tf, since, until=until)
        return [c for c in self.data.get(token, {}).get(tf, []) if since <= c.ts < until]


def run_backtest(cfg, instruments: list[Instrument], source: CandleSource, start: dt.date, end: dt.date, *,
                 pipeline: PipelineParams | None = None, profile: str = "realistic", specs: dict | None = None,
                 chain_params: ModelChainParams | None = None, history_days: int = 60, holidays=(), seed: int = 7,
                 dal: MemoryDAL | None = None) -> BacktestReport:
    """Replay every trading day in [start, end] through PaperLoop with
    execute=True and the model chain. One MemoryDAL journal for the whole
    run (one run_id per day), one AccountState across days."""
    specs = specs or DEFAULT_SPECS
    pipeline = pipeline or PipelineParams.from_config(cfg)
    dal = dal or MemoryDAL()
    report = BacktestReport(capital=cfg.capital, profile=profile)
    account = AccountState(equity=cfg.capital)
    spots = [i for i in instruments if i.role == "spot"]
    day = start
    week = None
    while day <= end:
        if not is_trading_day(day, holidays):
            day += dt.timedelta(days=1)
            continue
        open_at = session_open_at(day)
        since = open_at - dt.timedelta(days=history_days)
        stores: dict[tuple[str, str], CandleStore] = {}
        today_1m: dict[str, list] = {}
        for inst in instruments:
            for tf in TFS:
                st = CandleStore(tf)
                st.extend(source.load(inst.token, tf, since, open_at))
                stores[(inst.token, tf)] = st
            today_1m[inst.token] = source.load(inst.token, "1m", open_at, open_at + dt.timedelta(hours=7))
        if not any(today_1m[i.token] for i in spots):
            day += dt.timedelta(days=1)
            continue
        clock = SimClock(dt.datetime.combine(day, dt.time(9, 0), tzinfo=IST))
        src = TickReplaySource(today_1m, clock, day)
        chains = ModelChainService({i.underlying: specs[i.underlying] for i in spots if i.underlying in specs}, day, chain_params)
        broker = PaperBroker(PaperParams.for_profile(profile, seed=seed + day.toordinal()))
        run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"backtest/{day.isoformat()}/{profile}")
        loop = PaperLoop(cfg, dal, instruments, src, clock.now, None, stores, run_id=run_id, git_sha="backtest",
                         chains=chains, broker=broker, pipeline=pipeline, execute=True)
        # carry the account across days; reset the daily tallies (§34)
        iso_week = day.isocalendar()[:2]
        if week != iso_week:
            account.realized_week = 0.0
            account.weekly_lock = False
            week = iso_week
        account.realized_today = 0.0
        account.trades_today = 0
        account.daily_lock = False
        account.open_positions = 0
        account.open_risk = 0.0
        account.open_directions = {}
        loop.account = account
        stats = loop.run()
        rows = dal.trade_results_for_run(run_id)
        report.days.append(DayResult(day, str(run_id), stats.snapshots, stats.decisions, stats.approved, len(rows),
                                     round(sum(float(r["net_pnl"]) for r in rows), 2), summarize(loop.rejections)))
        report.trade_rows.extend(rows)
        report.signals.extend(dal.signals_for_run(run_id))
        for r in loop.rejections:
            report.rejections.setdefault("_all", []).append(r)
        day += dt.timedelta(days=1)
    allrej = report.rejections.pop("_all", [])
    report.rejections = summarize(allrej)
    report.metrics = compute(report.trade_rows, cfg.capital)
    return report
