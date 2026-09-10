import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TechnicalConfig:
    """Config for the second bot (run_technical.py) - fully independent of
    Config/run_daily.py's own settings (separate kill switches, separate
    capital, separate watchlists per tier). Auth/session still come from the
    main Config (same api_key/client_code/pin/totp_secret) - Angel One issues
    one API key per ACCOUNT, not per app (confirmed live), so a genuinely
    separate session isn't possible; see rest_client.py's SESSION_ERROR_CODES
    auto-relogin for how the two processes tolerate sharing one JWT.
    """

    dry_run: bool
    enable_trading: bool

    capital: float = 50_000.0

    entry_time: str = "09:20"  # a few minutes after open, for the first bars to form
    exit_time: str = "15:15"  # same-day close for scalp/intraday, matches run_daily.py

    # --- shared options-tier sizing/risk (scalp + intraday + index swing) ---
    # Raised again 0.20 -> 0.30 (with the three per-tier risk pcts below,
    # all 0.15 -> 0.30) 2026-09-09 evening, user's explicit instruction after
    # confirming live that a real intraday NIFTY signal at 14:03-14:07 that
    # day got blocked by the PRIOR Rs.7,500 budget too. Budget = min(risk_pct,
    # this) x capital, so effective max = Rs.15,000/trade on 50k capital now,
    # covering all but the very largest premiums seen so far (e.g. the
    # ~Rs.16k+ BANKNIFTY scalp spike). For a long option, max loss per trade
    # = premium paid, so this is ALSO the effective max loss per trade
    # (30% of capital) - fine for paper-mode learning, must be revisited
    # before ever setting TECH_DRY_RUN=false. History: 0.10 (initial) ->
    # 0.20 (first live-verified fix, same day) -> 0.30 (this).
    max_capital_pct_per_trade: float = 0.30
    max_lots_per_trade: int = 1  # 5 -> 1 2026-09-09 evening, user's explicit instruction - start conservative on size per trade
    max_spread_pct: float = 8.0
    min_open_interest: int = 100
    limit_order_buffer_pct: float = 0.5
    dte_min: int = 0
    dte_max: int = 45  # scalp/intraday: nearest available contract, same convention as run_daily.py
    otm_distance_pct: float = 0.01  # closer to ATM than run_daily.py's 0.02 - shorter-horizon signals

    # --- ATR-based stop/target/trailing-stop - SWING TIER ONLY as of
    # 2026-09-09 evening (scalp/intraday moved to a fixed rupee-per-lot
    # stop/target instead, see their own sections below - user's explicit
    # instruction after several scalp trades that day closed with a gross
    # P&L of only Rs.20-50/lot, too small to reliably clear real
    # transaction costs, see costs.py). Still used for the swing tier
    # (index option AND stock equity legs): the stop/target distance is
    # `mult x ATR(period)` on whichever price series the tier signals off
    # of (index level for the options leg, the stock's own price for
    # equity) - a calculated, volatility-scaled distance instead of a
    # single guessed percentage that's the same on a calm day as a violent
    # one. Swing's multi-day hold/bigger-expected-move profile is why it
    # keeps this instead of also moving to a fixed rupee floor. 1.5x stop /
    # 2.5x target (~1:1.7 reward:risk) and a 1x-ATR trail (activating once
    # 1x ATR in profit) are first-cut choices, same unvalidated-until-
    # backtested caveat as every other threshold in this file - see
    # technical_strategy.atr_stop_target's docstring.
    atr_period: int = 14
    atr_stop_mult: float = 1.5
    atr_target_mult: float = 2.5
    atr_trail_activate_mult: float = 1.0
    atr_trail_mult: float = 1.0

    # --- scenario-adaptive scaling on top of the base multipliers above ---
    # STOP side scales with volatility regime (current ATR vs its own
    # baseline_period_mult-x-longer baseline ATR); TARGET side scales with
    # trend strength (EMA(trend_fast_period)/EMA(trend_slow_period) spread as
    # a % of entry price). See technical_strategy.atr_stop_target's
    # docstring for the exact formula - both are first-cut, unbacktested
    # scaling choices, same caveat as the base multipliers themselves.
    atr_baseline_period_mult: int = 3
    atr_trend_fast_period: int = 9
    atr_trend_slow_period: int = 21
    atr_stop_scale_min: float = 0.7
    atr_stop_scale_max: float = 1.5
    atr_target_scale_min: float = 1.0
    atr_target_scale_max: float = 1.8

    # --- scalp tier (1-min, EMA9/21 x VWAP x volume) ---
    # Index-only as of 2026-09-09 evening ("lets do only index options") -
    # stocks dropped from the watchlist entirely.
    scalp_watchlist: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")
    scalp_risk_per_trade_pct: float = 0.30  # 0.02 -> 0.15 -> 0.30 2026-09-09, see max_capital_pct_per_trade's comment
    scalp_daily_loss_cap_pct: float = 0.05
    scalp_ema_fast: int = 9
    scalp_ema_slow: int = 21
    scalp_avg_volume_period: int = 20
    scalp_min_relative_volume: float = 1.5
    scalp_max_hold_minutes: int = 15
    scalp_max_trades_per_day: int = 3
    scalp_poll_seconds: int = 60  # a fresh 1-min bar every ~60s
    # Fixed rupee-per-lot stop/target (technical_strategy.premium_stop_target),
    # NOT ATR-based - see the ATR section's comment above for why. User's
    # explicit range was Rs.500-700 stop / Rs.500-800 target; picked the
    # midpoints. No trailing stop for this tier (see state.py's
    # OpenTechnicalOption docstring) - target is a hard take-profit level.
    scalp_stop_rupees_per_lot: float = 600.0
    scalp_target_rupees_per_lot: float = 700.0

    # --- intraday tier (5-min, EMA20/50 or pivot breakout) ---
    # Index-only as of 2026-09-09 evening, same reasoning as scalp above.
    intraday_watchlist: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")
    intraday_risk_per_trade_pct: float = 0.30  # 0.02 -> 0.15 -> 0.30 2026-09-09, see max_capital_pct_per_trade's comment
    intraday_daily_loss_cap_pct: float = 0.05
    intraday_ema_fast: int = 20
    intraday_ema_slow: int = 50
    intraday_rsi_period: int = 14
    intraday_avg_volume_period: int = 20
    intraday_min_relative_volume: float = 1.5
    intraday_max_trades_per_day: int = 1  # per underlying - "only first signal per day", matches the backtest
    intraday_bar_minutes: int = 5
    intraday_poll_seconds: int = 300
    # Same fixed rupee-per-lot mechanism as scalp above, own knobs since
    # intraday's larger bar size/hold could reasonably want a different
    # floor later even though the initial default matches scalp's.
    intraday_stop_rupees_per_lot: float = 600.0
    intraday_target_rupees_per_lot: float = 700.0

    # --- swing tier (daily, trend-reversal exit) ---
    # Raised alongside the other two tiers, 0.15 -> 0.30, 2026-09-09 evening
    # (see max_capital_pct_per_trade's comment) - a wide-DTE (20-60 day)
    # index option carries far more time value than a same-day contract, so
    # this tier needed raising first/most (history: 0.02 -> 0.15 live-
    # verified same day -> 0.30 this). For a long option, max loss per trade
    # = premium paid, so this is also the effective max loss per trade (30%
    # of capital) - fine for paper-mode learning, revisit before ever
    # setting TECH_DRY_RUN=false. Only affects the swing tier's INDEX/options
    # leg - the stock/equity leg sizes off swing_equity_budget_pct_per_trade
    # instead (unaffected by this or the max_capital_pct_per_trade change).
    swing_risk_per_trade_pct: float = 0.30
    swing_daily_loss_cap_pct: float = 0.05
    # False as of 2026-09-09 evening ("lets do only index options") - gates
    # ONLY new swing-equity entries (the F&O-eligible/volume-filtered stock
    # universe scan in run_technical._run_swing_scan); any equity positions
    # already open when this flipped keep being monitored/exited normally,
    # not force-closed - see run_technical.py's own comment at that gate.
    swing_equity_enabled: bool = False
    swing_min_volume: int = 500_000  # today's tradeVolume floor for a stock to enter the swing universe
    swing_sma_fast: int = 50
    swing_sma_slow: int = 200
    swing_rsi_period: int = 14
    swing_left_right: int = 5
    swing_sr_tolerance_pct: float = 1.0
    swing_sr_min_touches: int = 2
    swing_level_reclaim_buffer_pct: float = 2.0
    swing_max_hold_days: int = 90
    swing_equity_budget_pct_per_trade: float = 0.05  # exposure cap; risk sizing itself uses the ATR-derived stop distance
    swing_max_equity_positions: int = 10
    swing_index_dte_min: int = 20
    swing_index_dte_max: int = 60  # wide, expiry-safe window - explicitly NOT futures (see plan decision #1)
    swing_watchlist_indices: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")

    # --- real transaction costs (costs.py), deducted from every realized P&L ---
    # Added 2026-09-09 evening, user's explicit request ("consider charges as
    # well like gst cgst, stamp duty, brokerage charges") - motivated by
    # several scalp trades that day closing with a gross P&L of only
    # Rs.20-50/lot, which real costs could plausibly wipe out or reverse.
    #
    # HONESTY CAVEAT: every rate below is a first-cut, publicly-known-
    # structure DEFAULT, NOT verified against the user's actual Angel One
    # brokerage plan or today's statutory rates - both change periodically
    # (broker plan changes, budget announcements, exchange circulars).
    # Verify against a real contract note before trusting these for
    # anything beyond a rough paper-mode approximation - same honesty
    # convention as every other unvalidated threshold in this file.
    cost_brokerage_per_order: float = 20.0  # flat per executed F&O order - standard discount-broker rate
    cost_stt_sell_pct: float = 0.1  # options STT, SELL side only (we only ever buy, so this hits every exit)
    cost_exchange_txn_pct: float = 0.035  # NSE/BSE F&O transaction charges, both legs
    cost_sebi_fee_pct: float = 0.0001  # SEBI turnover fee (~Rs.10/crore), both legs
    cost_stamp_duty_pct: float = 0.003  # F&O stamp duty, BUY side only
    cost_gst_pct: float = 18.0  # on (brokerage + exchange txn + SEBI fee) only, not on STT/stamp duty
    cost_equity_brokerage_per_order: float = 0.0  # equity DELIVERY (CNC) is commission-free on most discount broker plans
    cost_equity_stt_pct: float = 0.1  # equity delivery STT applies to BOTH legs, unlike options' sell-only
    cost_equity_stamp_duty_pct: float = 0.015  # equity delivery stamp duty is higher than F&O's, BUY side only

    @classmethod
    def from_env(cls) -> "TechnicalConfig":
        def _tuple(name: str, default: str) -> tuple[str, ...]:
            raw = os.environ.get(name, default)
            return tuple(s.strip().upper() for s in raw.split(",") if s.strip())

        return cls(
            dry_run=os.environ.get("TECH_DRY_RUN", "true").lower() != "false",
            enable_trading=os.environ.get("TECH_ENABLE_TRADING", "false").lower() == "true",
            capital=float(os.environ.get("TECH_CAPITAL", "50000")),
            entry_time=os.environ.get("TECH_ENTRY_TIME", "09:20"),
            exit_time=os.environ.get("TECH_EXIT_TIME", "15:15"),
            max_capital_pct_per_trade=float(os.environ.get("TECH_MAX_CAPITAL_PCT_PER_TRADE", "0.30")),
            max_lots_per_trade=int(os.environ.get("TECH_MAX_LOTS_PER_TRADE", "1")),
            max_spread_pct=float(os.environ.get("TECH_MAX_SPREAD_PCT", "8.0")),
            min_open_interest=int(os.environ.get("TECH_MIN_OPEN_INTEREST", "100")),
            limit_order_buffer_pct=float(os.environ.get("TECH_LIMIT_ORDER_BUFFER_PCT", "0.5")),
            dte_min=int(os.environ.get("TECH_DTE_MIN", "0")),
            dte_max=int(os.environ.get("TECH_DTE_MAX", "45")),
            otm_distance_pct=float(os.environ.get("TECH_OTM_DISTANCE_PCT", "0.01")),
            atr_period=int(os.environ.get("TECH_ATR_PERIOD", "14")),
            atr_stop_mult=float(os.environ.get("TECH_ATR_STOP_MULT", "1.5")),
            atr_target_mult=float(os.environ.get("TECH_ATR_TARGET_MULT", "2.5")),
            atr_trail_activate_mult=float(os.environ.get("TECH_ATR_TRAIL_ACTIVATE_MULT", "1.0")),
            atr_trail_mult=float(os.environ.get("TECH_ATR_TRAIL_MULT", "1.0")),
            atr_baseline_period_mult=int(os.environ.get("TECH_ATR_BASELINE_PERIOD_MULT", "3")),
            atr_trend_fast_period=int(os.environ.get("TECH_ATR_TREND_FAST_PERIOD", "9")),
            atr_trend_slow_period=int(os.environ.get("TECH_ATR_TREND_SLOW_PERIOD", "21")),
            atr_stop_scale_min=float(os.environ.get("TECH_ATR_STOP_SCALE_MIN", "0.7")),
            atr_stop_scale_max=float(os.environ.get("TECH_ATR_STOP_SCALE_MAX", "1.5")),
            atr_target_scale_min=float(os.environ.get("TECH_ATR_TARGET_SCALE_MIN", "1.0")),
            atr_target_scale_max=float(os.environ.get("TECH_ATR_TARGET_SCALE_MAX", "1.8")),
            scalp_watchlist=_tuple("TECH_SCALP_WATCHLIST", "NIFTY,BANKNIFTY,SENSEX"),
            scalp_risk_per_trade_pct=float(os.environ.get("TECH_SCALP_RISK_PER_TRADE_PCT", "0.30")),
            scalp_daily_loss_cap_pct=float(os.environ.get("TECH_SCALP_DAILY_LOSS_CAP_PCT", "0.05")),
            scalp_ema_fast=int(os.environ.get("TECH_SCALP_EMA_FAST", "9")),
            scalp_ema_slow=int(os.environ.get("TECH_SCALP_EMA_SLOW", "21")),
            scalp_avg_volume_period=int(os.environ.get("TECH_SCALP_AVG_VOLUME_PERIOD", "20")),
            scalp_min_relative_volume=float(os.environ.get("TECH_SCALP_MIN_RELATIVE_VOLUME", "1.5")),
            scalp_max_hold_minutes=int(os.environ.get("TECH_SCALP_MAX_HOLD_MINUTES", "15")),
            scalp_max_trades_per_day=int(os.environ.get("TECH_SCALP_MAX_TRADES_PER_DAY", "3")),
            scalp_poll_seconds=int(os.environ.get("TECH_SCALP_POLL_SECONDS", "60")),
            scalp_stop_rupees_per_lot=float(os.environ.get("TECH_SCALP_STOP_RUPEES_PER_LOT", "600.0")),
            scalp_target_rupees_per_lot=float(os.environ.get("TECH_SCALP_TARGET_RUPEES_PER_LOT", "700.0")),
            intraday_watchlist=_tuple("TECH_INTRADAY_WATCHLIST", "NIFTY,BANKNIFTY,SENSEX"),
            intraday_risk_per_trade_pct=float(os.environ.get("TECH_INTRADAY_RISK_PER_TRADE_PCT", "0.30")),
            intraday_daily_loss_cap_pct=float(os.environ.get("TECH_INTRADAY_DAILY_LOSS_CAP_PCT", "0.05")),
            intraday_ema_fast=int(os.environ.get("TECH_INTRADAY_EMA_FAST", "20")),
            intraday_ema_slow=int(os.environ.get("TECH_INTRADAY_EMA_SLOW", "50")),
            intraday_rsi_period=int(os.environ.get("TECH_INTRADAY_RSI_PERIOD", "14")),
            intraday_avg_volume_period=int(os.environ.get("TECH_INTRADAY_AVG_VOLUME_PERIOD", "20")),
            intraday_min_relative_volume=float(os.environ.get("TECH_INTRADAY_MIN_RELATIVE_VOLUME", "1.5")),
            intraday_max_trades_per_day=int(os.environ.get("TECH_INTRADAY_MAX_TRADES_PER_DAY", "1")),
            intraday_bar_minutes=int(os.environ.get("TECH_INTRADAY_BAR_MINUTES", "5")),
            intraday_poll_seconds=int(os.environ.get("TECH_INTRADAY_POLL_SECONDS", "300")),
            intraday_stop_rupees_per_lot=float(os.environ.get("TECH_INTRADAY_STOP_RUPEES_PER_LOT", "600.0")),
            intraday_target_rupees_per_lot=float(os.environ.get("TECH_INTRADAY_TARGET_RUPEES_PER_LOT", "700.0")),
            swing_risk_per_trade_pct=float(os.environ.get("TECH_SWING_RISK_PER_TRADE_PCT", "0.30")),
            swing_daily_loss_cap_pct=float(os.environ.get("TECH_SWING_DAILY_LOSS_CAP_PCT", "0.05")),
            swing_equity_enabled=os.environ.get("TECH_SWING_EQUITY_ENABLED", "false").lower() == "true",
            swing_min_volume=int(os.environ.get("TECH_SWING_MIN_VOLUME", "500000")),
            swing_sma_fast=int(os.environ.get("TECH_SWING_SMA_FAST", "50")),
            swing_sma_slow=int(os.environ.get("TECH_SWING_SMA_SLOW", "200")),
            swing_rsi_period=int(os.environ.get("TECH_SWING_RSI_PERIOD", "14")),
            swing_left_right=int(os.environ.get("TECH_SWING_LEFT_RIGHT", "5")),
            swing_sr_tolerance_pct=float(os.environ.get("TECH_SWING_SR_TOLERANCE_PCT", "1.0")),
            swing_sr_min_touches=int(os.environ.get("TECH_SWING_SR_MIN_TOUCHES", "2")),
            swing_level_reclaim_buffer_pct=float(os.environ.get("TECH_SWING_LEVEL_RECLAIM_BUFFER_PCT", "2.0")),
            swing_max_hold_days=int(os.environ.get("TECH_SWING_MAX_HOLD_DAYS", "90")),
            swing_equity_budget_pct_per_trade=float(os.environ.get("TECH_SWING_EQUITY_BUDGET_PCT_PER_TRADE", "0.05")),
            swing_max_equity_positions=int(os.environ.get("TECH_SWING_MAX_EQUITY_POSITIONS", "10")),
            swing_index_dte_min=int(os.environ.get("TECH_SWING_INDEX_DTE_MIN", "20")),
            swing_index_dte_max=int(os.environ.get("TECH_SWING_INDEX_DTE_MAX", "60")),
            swing_watchlist_indices=_tuple("TECH_SWING_WATCHLIST_INDICES", "NIFTY,BANKNIFTY,SENSEX"),
            cost_brokerage_per_order=float(os.environ.get("TECH_COST_BROKERAGE_PER_ORDER", "20.0")),
            cost_stt_sell_pct=float(os.environ.get("TECH_COST_STT_SELL_PCT", "0.1")),
            cost_exchange_txn_pct=float(os.environ.get("TECH_COST_EXCHANGE_TXN_PCT", "0.035")),
            cost_sebi_fee_pct=float(os.environ.get("TECH_COST_SEBI_FEE_PCT", "0.0001")),
            cost_stamp_duty_pct=float(os.environ.get("TECH_COST_STAMP_DUTY_PCT", "0.003")),
            cost_gst_pct=float(os.environ.get("TECH_COST_GST_PCT", "18.0")),
            cost_equity_brokerage_per_order=float(os.environ.get("TECH_COST_EQUITY_BROKERAGE_PER_ORDER", "0.0")),
            cost_equity_stt_pct=float(os.environ.get("TECH_COST_EQUITY_STT_PCT", "0.1")),
            cost_equity_stamp_duty_pct=float(os.environ.get("TECH_COST_EQUITY_STAMP_DUTY_PCT", "0.015")),
        )
