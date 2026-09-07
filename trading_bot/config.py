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
        )
