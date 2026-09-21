"""Engine configuration - every TECH_* environment variable, parsed once
into a frozen dataclass. The broker-level Config (SmartAPI credentials,
URLs) is shared with the daily bot and read via trading_bot.config; this
module only adds the second bot's own settings on top.

Mode semantics (spec §50-51): the same engine code runs in every mode.
Only LIVE can ever reach a real order endpoint, and only when all three
switches agree - TECH_MODE=LIVE, TECH_LIVE_TRADING_ENABLED=true and
TECH_DRY_RUN=false. Any other combination forces dry_run=True on the
broker config, which makes RestClient refuse order-mutating calls at the
lowest layer (paper mode must make live orders technically impossible,
not just discouraged). tests/test_config_bounds.py pins TECH_DRY_RUN=true
and TECH_LIVE_TRADING_ENABLED=false in deploy/config.env, so flipping to
LIVE is a deliberate human change that the nightly tuner cannot make.

Every threshold below is a first-cut, unvalidated starting value - the
spec's own posture (§4 "do not assume a regime is profitable", §8 "weights
must be validated statistically"). They exist as config so M3's validation
can move them without code changes.
"""
import dataclasses
import datetime as dt
import os
from dataclasses import dataclass
from typing import Literal

from trading_bot.config import Config

Mode = Literal["BACKTEST", "RESEARCH", "PAPER", "SHADOW", "LIVE"]
MODES: tuple[str, ...] = ("BACKTEST", "RESEARCH", "PAPER", "SHADOW", "LIVE")

TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "30m", "1d")


def _hhmm(value: str) -> dt.time:
    return dt.datetime.strptime(value, "%H:%M").time()


def _bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() == "true"


def _float(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def _int(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(s.strip().upper() for s in os.environ.get(name, default).split(",") if s.strip())


@dataclass(frozen=True)
class EngineConfig:
    mode: str
    dry_run: bool
    live_trading_enabled: bool
    database_url: str
    underlyings: tuple[str, ...]
    capital: float

    # --- hard risk (spec §25, §34). Bounded in tests/test_config_bounds.py. ---
    risk_per_trade_pct: float
    daily_loss_cap_pct: float
    weekly_loss_cap_pct: float

    # --- session clock (IST) ---
    eod_cutoff: dt.time  # no new entries after this; M2 flattens positions here
    session_end: dt.time  # last bar closes; engine flushes and exits
    eod_summary_time: dt.time

    # --- warmup lookbacks, calendar days per timeframe ---
    warmup_days_1m: int
    warmup_days_5m: int
    warmup_days_30m: int
    warmup_days_1d: int

    # --- feed / data quality (§58, §86) ---
    stale_tick_seconds: int
    feed_backoff_max_seconds: int
    clock_drift_seconds: int
    volume_proxy: str  # "futures" - index ticks carry volume=0

    # --- trend alignment weights (§8) ---
    align_w_daily: float
    align_w_30m: float
    align_w_5m: float
    align_w_1m: float

    # --- trend / regime thresholds (§4-5) ---
    trend_adx_min: float
    trend_adx_strong: float
    regime_atr_pct_high: float
    regime_atr_pct_low: float
    bb_compression_pct: float

    # --- pre-signal (§13) ---
    presignal_ttl_bars: int
    presignal_decay: float
    presignal_min_conf: float
    level_proximity_atr: float

    # --- notifications ---
    telegram_max_alerts_per_hour: int

    holidays: tuple[str, ...]  # ISO dates
    # --- M2: decision pipeline + paper execution (spec §16, §19, §25, §29, §34, §52) ---
    paper_profile: str = "realistic"  # realistic | conservative | ideal (ideal is never proof of anything, §52)
    min_score: int = 40
    preferred_net_reward: float = 800.0
    tier_b_enabled: bool = True
    tier_b_min_score: int = 70
    max_trades_per_day: int = 6
    max_open_positions: int = 2
    max_consecutive_losses: int = 3
    option_delta_min: float = 0.30
    option_delta_max: float = 0.65
    option_max_spread_pct: float = 2.0
    option_min_oi: int = 5000
    option_dte_max: int = 14
    option_expiry_day_allowed: bool = False
    chain_refresh_seconds: int = 60
    chain_strikes_each_side: int = 6
    # phase 24: strategies that run the full pipeline and are journaled as valid
    # signals but are never executed in PAPER (nor, later, LIVE)
    shadow_strategies: tuple[str, ...] = ()
    # §7 / §88 "counter-trend threshold": which higher timeframes make a trade counter-trend
    # ("30m", "1d", both, or none) and how many evidence keys a counter-trend setup needs
    counter_trend_veto_tfs: tuple[str, ...] = ("30m",)
    counter_trend_min_evidence: int = 6

    @property
    def can_place_live_orders(self) -> bool:
        return self.mode == "LIVE" and self.live_trading_enabled and not self.dry_run

    @property
    def align_weights(self) -> dict[str, float]:
        return {"1d": self.align_w_daily, "30m": self.align_w_30m, "5m": self.align_w_5m, "1m": self.align_w_1m}

    @classmethod
    def from_env(cls) -> "EngineConfig":
        mode = os.environ.get("TECH_MODE", "SHADOW").strip().upper()
        if mode not in MODES:
            raise RuntimeError(f"TECH_MODE={mode!r} is not one of {MODES}")
        weights = (
            _float("TECH_ALIGN_W_DAILY", "0.25"), _float("TECH_ALIGN_W_30M", "0.30"),
            _float("TECH_ALIGN_W_5M", "0.30"), _float("TECH_ALIGN_W_1M", "0.15"),
        )
        if abs(sum(weights) - 1.0) > 1e-6:
            raise RuntimeError(f"TECH_ALIGN_W_* must sum to 1.0, got {sum(weights):.4f}")
        return cls(
            mode=mode,
            dry_run=os.environ.get("TECH_DRY_RUN", "true").strip().lower() != "false",
            live_trading_enabled=_bool("TECH_LIVE_TRADING_ENABLED", "false"),
            database_url=os.environ.get("TECH_DATABASE_URL", ""),
            underlyings=_csv("TECH_UNDERLYINGS", "NIFTY,BANKNIFTY,SENSEX"),
            capital=_float("TECH_CAPITAL", "50000"),
            risk_per_trade_pct=_float("TECH_RISK_PER_TRADE_PCT", "0.005"),
            daily_loss_cap_pct=_float("TECH_DAILY_LOSS_CAP_PCT", "0.02"),
            weekly_loss_cap_pct=_float("TECH_WEEKLY_LOSS_CAP_PCT", "0.05"),
            eod_cutoff=_hhmm(os.environ.get("TECH_EOD_CUTOFF", "15:20")),
            session_end=_hhmm(os.environ.get("TECH_SESSION_END", "15:30")),
            eod_summary_time=_hhmm(os.environ.get("TECH_EOD_SUMMARY_TIME", "15:35")),
            warmup_days_1m=_int("TECH_WARMUP_DAYS_1M", "7"),
            warmup_days_5m=_int("TECH_WARMUP_DAYS_5M", "21"),
            warmup_days_30m=_int("TECH_WARMUP_DAYS_30M", "90"),
            warmup_days_1d=_int("TECH_WARMUP_DAYS_1D", "400"),
            stale_tick_seconds=_int("TECH_STALE_TICK_SECONDS", "15"),
            feed_backoff_max_seconds=_int("TECH_FEED_BACKOFF_MAX_SECONDS", "60"),
            clock_drift_seconds=_int("TECH_CLOCK_DRIFT_SECONDS", "5"),
            volume_proxy=os.environ.get("TECH_VOLUME_PROXY", "futures").strip().lower(),
            align_w_daily=weights[0], align_w_30m=weights[1], align_w_5m=weights[2], align_w_1m=weights[3],
            trend_adx_min=_float("TECH_TREND_ADX_MIN", "20"),
            trend_adx_strong=_float("TECH_TREND_ADX_STRONG", "25"),
            regime_atr_pct_high=_float("TECH_REGIME_ATR_PCT_HIGH", "80"),
            regime_atr_pct_low=_float("TECH_REGIME_ATR_PCT_LOW", "20"),
            bb_compression_pct=_float("TECH_BB_COMPRESSION_PCT", "20"),
            presignal_ttl_bars=_int("TECH_PRESIGNAL_TTL_BARS", "6"),
            presignal_decay=_float("TECH_PRESIGNAL_DECAY", "0.85"),
            presignal_min_conf=_float("TECH_PRESIGNAL_MIN_CONF", "0.35"),
            level_proximity_atr=_float("TECH_LEVEL_PROXIMITY_ATR", "0.5"),
            telegram_max_alerts_per_hour=_int("TECH_TELEGRAM_MAX_ALERTS_PER_HOUR", "12"),
            holidays=tuple(s.strip() for s in os.environ.get("TECH_HOLIDAYS", "").split(",") if s.strip()),
            paper_profile=os.environ.get("TECH_PAPER_PROFILE", "realistic").strip().lower(),
            min_score=_int("TECH_MIN_SCORE", "40"),
            preferred_net_reward=_float("TECH_PREFERRED_NET_REWARD", "800"),
            tier_b_enabled=_bool("TECH_TIER_B_ENABLED", "true"),
            tier_b_min_score=_int("TECH_TIER_B_MIN_SCORE", "70"),
            max_trades_per_day=_int("TECH_MAX_TRADES_PER_DAY", "6"),
            max_open_positions=_int("TECH_MAX_OPEN_POSITIONS", "2"),
            max_consecutive_losses=_int("TECH_MAX_CONSECUTIVE_LOSSES", "3"),
            option_delta_min=_float("TECH_OPTION_DELTA_MIN", "0.30"),
            option_delta_max=_float("TECH_OPTION_DELTA_MAX", "0.65"),
            option_max_spread_pct=_float("TECH_OPTION_MAX_SPREAD_PCT", "2.0"),
            option_min_oi=_int("TECH_OPTION_MIN_OI", "5000"),
            option_dte_max=_int("TECH_OPTION_DTE_MAX", "14"),
            option_expiry_day_allowed=_bool("TECH_OPTION_EXPIRY_DAY_ALLOWED", "false"),
            chain_refresh_seconds=_int("TECH_CHAIN_REFRESH_SECONDS", "60"),
            chain_strikes_each_side=_int("TECH_CHAIN_STRIKES_EACH_SIDE", "6"),
            shadow_strategies=_csv("TECH_SHADOW_STRATEGIES", ""),
            counter_trend_veto_tfs=tuple(t.strip().lower() for t in os.environ.get("TECH_COUNTER_TREND_VETO_TFS", "30m").split(",") if t.strip()),
            counter_trend_min_evidence=_int("TECH_COUNTER_TREND_MIN_EVIDENCE", "6"),
        )


def broker_config(engine: EngineConfig, base: Config | None = None) -> Config:
    """The SmartAPI Config this engine's Session/RestClient should use.

    dry_run is forced True unless the engine is genuinely cleared for live
    orders - so a SHADOW/PAPER/BACKTEST process can never reach placeOrder
    even if someone edits the shared DRY_RUN the daily bot reads.
    """
    base = base or Config.from_env()
    return dataclasses.replace(base, dry_run=not engine.can_place_live_orders)
