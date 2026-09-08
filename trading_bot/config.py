import os
import socket
import uuid
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

load_dotenv()


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _public_ip(fallback: str) -> str:
    try:
        resp = requests.get("https://api.ipify.org", timeout=5)
        resp.raise_for_status()
        return resp.text.strip()
    except requests.RequestException:
        return fallback


def _mac_address() -> str:
    mac = uuid.getnode()
    return "-".join(f"{(mac >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))


@dataclass(frozen=True)
class Config:
    api_key: str
    client_code: str
    pin: str
    totp_secret: str
    dry_run: bool
    local_ip: str
    public_ip: str
    mac_address: str

    root_url: str = "https://apiconnect.angelone.in"
    ws_market_url: str = "wss://smartapisocket.angelone.in/smart-stream"
    ws_order_url: str = "wss://tns.angelone.in/smart-order-update"
    scrip_master_url: str = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

    # --- iron condor strategy settings ---
    # DTE window is deliberately wide by default: this is a DAILY intraday
    # strategy, not a near-expiry-only one. Whether to trade a given day is
    # decided by market_filter.TradeFilter (VIX/PCR/OI), not by proximity to
    # expiry - the nearest available contract inside this window is used
    # every day the filter passes. Narrow this back down if you specifically
    # want to trade only near expiry again.
    dte_min: int = 0
    dte_max: int = 45
    entry_time: str = "12:30"
    exit_time: str = "15:15"
    otm_distance_pct: float = 0.02
    wing_distance_pct: float = 0.01
    watchlist: tuple[str, ...] = ("NIFTY", "BANKNIFTY")
    enable_trading: bool = False  # separate kill switch from dry_run - must be explicitly set true

    # --- capital / paper-trading sizing ---
    # Paper capital the strategy is sized against. Position size per trade is
    # computed from this via the broker's own margin calculator, not fixed -
    # see sizing.py. Risk caps (risk.py) are also a percentage of the running
    # capital ledger, not of this starting figure.
    capital: float = 50_000.0
    risk_per_trade_pct: float = 0.02  # max loss on one trade before its stop fires
    daily_loss_cap_pct: float = 0.05  # stop opening new trades for the day past this
    max_capital_pct_per_trade: float = 0.30  # cap on margin blocked by any one trade
    max_lots_per_trade: int = 5  # hard ceiling regardless of capital

    # --- daily entry filter (market_filter.TradeFilter) ---
    # First-cut, unvalidated thresholds - see market_filter.py's docstring.
    vix_min: float = 11.0
    vix_max: float = 25.0
    pcr_min: float = 0.7
    pcr_max: float = 1.3

    # --- long-option direction signal (debit_strategy.py) ---
    # Fallback used when OI buildup has no signal for an underlying - which
    # is ALWAYS true for indices (verified live: OIBuildup only ever
    # includes stock futures, never NIFTY/BANKNIFTY). Simple momentum vs
    # today's open; unvalidated starting threshold.
    momentum_min_move_pct: float = 0.15

    # --- pre-market bias (premarket_bias.py) ---
    # Genuinely LEADING signal (computed once, before the entry window,
    # from overnight US close + VIX + today's economic calendar) - unlike
    # the OI-buildup/momentum signal above, which is inherently lagging.
    # Gates entries by default per an explicit user choice ("take both" -
    # informational AND gating) - set to false to make it informational only.
    premarket_bias_gate_enabled: bool = True
    us_move_threshold_pct: float = 0.5
    vix_caution_level: float = 20.0

    # --- losing-streak circuit breaker ---
    # Bounded, safe automatic risk reduction after a run of losing days -
    # deliberately NOT auto-tuning entry signals/thresholds from this data
    # (real overfitting risk on a handful of days); just reduces size until
    # a human reviews the journal and decides on a real change. Tracked via
    # ledger["losing_streak_days"], persisted across restarts.
    losing_streak_cooldown_days: int = 3
    losing_streak_risk_multiplier: float = 0.5

    # --- liquidity check + depth-aware pricing (liquidity.py) ---
    # Before entering, check the SPECIFIC contract's own open interest and
    # bid-ask depth (not just the underlying's) - a thin contract can have a
    # wide spread even when the underlying itself is liquid. If it passes,
    # use a LIMIT order priced off the book instead of an unbounded MARKET
    # order. First-cut thresholds, not backtested.
    max_spread_pct: float = 8.0
    min_open_interest: int = 100
    limit_order_buffer_pct: float = 0.5

    # --- option-chain snapshot logging (option_chain_logger.py) ---
    # SmartAPI has no historical data for expired option contracts (see
    # research/README.md) - this is the only way to accumulate real premium
    # history going forward, for a future premium-based backtest. Pure data
    # collection: never affects trading decisions, never disabled by DRY_RUN.
    option_chain_log_enabled: bool = True
    option_chain_log_interval_seconds: int = 300
    option_chain_log_strike_band_pct: float = 0.15  # +-15% of spot around ATM

    # --- candle history logging (candle_history_logger.py) ---
    # Real OHLCV+OI candles (via getCandleData/getOIData, not point-sample
    # quotes like option_chain_logger above) for a FIXED, small band of
    # near-ATM strikes, at 1/5/10/30-minute and daily granularity - explicit
    # user request ("in future research and making strategies we can have
    # better things to do if we have more data"). Each interval pulls on
    # its own staggered cadence (candle_history_logger.INTERVAL_CONFIG) to
    # stay well within getCandleData's 3 req/sec, 5000/day limit - see that
    # module's docstring for the budget math. Pure data collection, same as
    # option_chain_log above: never affects trading decisions.
    candle_log_enabled: bool = True
    candle_log_strikes_each_side: int = 2  # 2 -> 5 strikes (ATM +-2) x CE/PE = 10 contracts/underlying

    # --- scalp add-on (scalp_strategy.py) ---
    # Explicit user request: an ADD-ON alongside the existing daily strategy,
    # not a replacement - "keep the existing behaviour as it is, and add
    # scalping as an add-on if there is a possibility of scalp". Off by
    # default like every other kill switch in this config; uses its OWN risk
    # budget/daily loss cap (separate DailyRiskTracker instance, same
    # capital ledger) so it can never affect the main strategy's risk
    # tracking. Entry signal is opening-range breakout (Zerodha's ORB
    # research, research/README.md finding #1) OR a short-window momentum
    # spike, whichever fires first (user: "combination of both and the
    # best"). Exit is a hard time-based hold (user: "minutes, true scalp") -
    # unconditional, whether winning or losing, capped by a stop-loss as a
    # safety backstop if it moves against it hard before the timer expires.
    scalp_enabled: bool = False
    scalp_risk_per_trade_pct: float = 0.01
    scalp_daily_loss_cap_pct: float = 0.03
    scalp_max_hold_minutes: int = 5
    scalp_max_trades_per_day: int = 3
    scalp_orb_ref_start: str = "09:15"
    scalp_orb_ref_end: str = "11:15"
    scalp_momentum_window_minutes: int = 5
    scalp_momentum_min_move_pct: float = 0.1

    @classmethod
    def from_env(cls) -> "Config":
        missing = [
            name
            for name in ("SMARTAPI_KEY", "SMARTAPI_CLIENT_CODE", "SMARTAPI_PIN", "SMARTAPI_TOTP_SECRET")
            if not os.environ.get(name)
        ]
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)} (see .env.example)")

        local_ip = _local_ip()
        watchlist_raw = os.environ.get("WATCHLIST", "NIFTY,BANKNIFTY")
        watchlist = tuple(s.strip().upper() for s in watchlist_raw.split(",") if s.strip())

        return cls(
            api_key=os.environ["SMARTAPI_KEY"],
            client_code=os.environ["SMARTAPI_CLIENT_CODE"],
            pin=os.environ["SMARTAPI_PIN"],
            totp_secret=os.environ["SMARTAPI_TOTP_SECRET"],
            dry_run=os.environ.get("DRY_RUN", "true").lower() != "false",
            local_ip=local_ip,
            public_ip=_public_ip(fallback=local_ip),
            mac_address=_mac_address(),
            dte_min=int(os.environ.get("DTE_MIN", "0")),
            dte_max=int(os.environ.get("DTE_MAX", "45")),
            entry_time=os.environ.get("ENTRY_TIME", "12:30"),
            exit_time=os.environ.get("EXIT_TIME", "15:15"),
            otm_distance_pct=float(os.environ.get("OTM_DISTANCE_PCT", "0.02")),
            wing_distance_pct=float(os.environ.get("WING_DISTANCE_PCT", "0.01")),
            watchlist=watchlist,
            enable_trading=os.environ.get("ENABLE_TRADING", "false").lower() == "true",
            capital=float(os.environ.get("CAPITAL", "50000")),
            risk_per_trade_pct=float(os.environ.get("RISK_PER_TRADE_PCT", "0.02")),
            daily_loss_cap_pct=float(os.environ.get("DAILY_LOSS_CAP_PCT", "0.05")),
            max_capital_pct_per_trade=float(os.environ.get("MAX_CAPITAL_PCT_PER_TRADE", "0.30")),
            max_lots_per_trade=int(os.environ.get("MAX_LOTS_PER_TRADE", "5")),
            vix_min=float(os.environ.get("VIX_MIN", "11")),
            vix_max=float(os.environ.get("VIX_MAX", "25")),
            pcr_min=float(os.environ.get("PCR_MIN", "0.7")),
            pcr_max=float(os.environ.get("PCR_MAX", "1.3")),
            momentum_min_move_pct=float(os.environ.get("MOMENTUM_MIN_MOVE_PCT", "0.15")),
            premarket_bias_gate_enabled=os.environ.get("PREMARKET_BIAS_GATE_ENABLED", "true").lower() == "true",
            us_move_threshold_pct=float(os.environ.get("US_MOVE_THRESHOLD_PCT", "0.5")),
            vix_caution_level=float(os.environ.get("VIX_CAUTION_LEVEL", "20")),
            losing_streak_cooldown_days=int(os.environ.get("LOSING_STREAK_COOLDOWN_DAYS", "3")),
            losing_streak_risk_multiplier=float(os.environ.get("LOSING_STREAK_RISK_MULTIPLIER", "0.5")),
            max_spread_pct=float(os.environ.get("MAX_SPREAD_PCT", "8.0")),
            min_open_interest=int(os.environ.get("MIN_OPEN_INTEREST", "100")),
            limit_order_buffer_pct=float(os.environ.get("LIMIT_ORDER_BUFFER_PCT", "0.5")),
            option_chain_log_enabled=os.environ.get("OPTION_CHAIN_LOG_ENABLED", "true").lower() != "false",
            option_chain_log_interval_seconds=int(os.environ.get("OPTION_CHAIN_LOG_INTERVAL_SECONDS", "300")),
            option_chain_log_strike_band_pct=float(os.environ.get("OPTION_CHAIN_LOG_STRIKE_BAND_PCT", "0.15")),
            candle_log_enabled=os.environ.get("CANDLE_LOG_ENABLED", "true").lower() != "false",
            candle_log_strikes_each_side=int(os.environ.get("CANDLE_LOG_STRIKES_EACH_SIDE", "2")),
            scalp_enabled=os.environ.get("SCALP_ENABLED", "false").lower() == "true",
            scalp_risk_per_trade_pct=float(os.environ.get("SCALP_RISK_PER_TRADE_PCT", "0.01")),
            scalp_daily_loss_cap_pct=float(os.environ.get("SCALP_DAILY_LOSS_CAP_PCT", "0.03")),
            scalp_max_hold_minutes=int(os.environ.get("SCALP_MAX_HOLD_MINUTES", "5")),
            scalp_max_trades_per_day=int(os.environ.get("SCALP_MAX_TRADES_PER_DAY", "3")),
            scalp_orb_ref_start=os.environ.get("SCALP_ORB_REF_START", "09:15"),
            scalp_orb_ref_end=os.environ.get("SCALP_ORB_REF_END", "11:15"),
            scalp_momentum_window_minutes=int(os.environ.get("SCALP_MOMENTUM_WINDOW_MINUTES", "5")),
            scalp_momentum_min_move_pct=float(os.environ.get("SCALP_MOMENTUM_MIN_MOVE_PCT", "0.1")),
        )
