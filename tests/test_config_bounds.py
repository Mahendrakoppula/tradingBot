"""Hard bounds on deploy/config.env, enforced in CI.

This exists because an autonomous nightly tuning agent (see
deploy/daily_review_prompt.md) edits config.env and pushes to main, and
main auto-deploys to the live instance. .github/workflows/deploy.yml
declares `deploy: needs: test`, so a failure here blocks the deploy
entirely - which makes this file the actual enforcement boundary on what
that agent can ship, rather than relying on it to follow instructions.

The agent is free to tune anything inside these ranges. It cannot flip a
kill switch, remove a risk cap, or set a parameter somewhere absurd,
because CI will refuse to deploy the change. Widen a bound here
deliberately if you decide a wider range is genuinely wanted - but do
that as a human, in a reviewed commit.
"""
import re
from pathlib import Path

import pytest

CONFIG_ENV_PATH = Path(__file__).resolve().parent.parent / "deploy" / "config.env"

# name -> (min, max) inclusive. Anything numeric the tuner may touch should
# have an entry; anything absent from here is simply not bounds-checked.
NUMERIC_BOUNDS = {
    # --- risk. The tight ones. ---
    "RISK_PER_TRADE_PCT": (0.005, 0.15),      # max loss per trade = premium paid on a long option
    "DAILY_LOSS_CAP_PCT": (0.01, 0.15),
    "MAX_CAPITAL_PCT_PER_TRADE": (0.05, 0.50),
    "MAX_LOTS_PER_TRADE": (1, 20),
    "CAPITAL": (1000, 10_000_000),
    "SCALP_RISK_PER_TRADE_PCT": (0.002, 0.05),
    "SCALP_DAILY_LOSS_CAP_PCT": (0.005, 0.10),
    "SCALP_MAX_TRADES_PER_DAY": (1, 20),
    "LOSING_STREAK_COOLDOWN_DAYS": (1, 30),
    "LOSING_STREAK_RISK_MULTIPLIER": (0.1, 1.0),
    # --- signal thresholds. Loose - this is what the tuner is FOR. ---
    "MOMENTUM_MIN_MOVE_PCT": (0.01, 2.0),
    "OTM_DISTANCE_PCT": (0.0, 0.10),
    "WING_DISTANCE_PCT": (0.0, 0.10),
    "US_MOVE_THRESHOLD_PCT": (0.1, 5.0),
    "VIX_CAUTION_LEVEL": (10.0, 60.0),
    "VIX_MIN": (5.0, 30.0),
    "VIX_MAX": (10.0, 80.0),
    "PCR_MIN": (0.1, 1.5),
    "PCR_MAX": (0.5, 3.0),
    "SECTOR_MOVE_THRESHOLD_PCT": (0.05, 3.0),
    "SCALP_MOMENTUM_MIN_MOVE_PCT": (0.01, 2.0),
    "SCALP_MAX_HOLD_MINUTES": (1, 120),
    "SCALP_MOMENTUM_WINDOW_MINUTES": (1, 60),
    "DTE_MIN": (0, 30),
    "DTE_MAX": (0, 90),
    # --- execution quality. Loosening these too far means taking
    # unfillable/illiquid contracts, so they're bounded too. ---
    "MAX_SPREAD_PCT": (1.0, 25.0),
    "MIN_OPEN_INTEREST": (0, 100_000),
    "LIMIT_ORDER_BUFFER_PCT": (0.0, 5.0),
    # --- data collection cadence. Bounded to stay inside API rate limits. ---
    "SECTOR_REFRESH_SECONDS": (60, 3600),
    "SECTOR_NOTIFY_INTERVAL_SECONDS": (300, 86400),
    "OPTION_CHAIN_LOG_INTERVAL_SECONDS": (60, 3600),
    "OPTION_CHAIN_LOG_STRIKE_BAND_PCT": (0.02, 0.50),
    "CANDLE_LOG_STRIKES_EACH_SIDE": (1, 10),
    # --- second bot (TECH_*). Spec §25: per-trade risk 0.25%-1.0% ALL-IN;
    # the caps are hard limits the spec calls non-negotiable. ---
    "TECH_CAPITAL": (10_000, 10_000_000),
    "TECH_RISK_PER_TRADE_PCT": (0.0025, 0.01),
    "TECH_DAILY_LOSS_CAP_PCT": (0.005, 0.03),
    "TECH_WEEKLY_LOSS_CAP_PCT": (0.01, 0.06),
    "TECH_WARMUP_DAYS_1M": (3, 28),
    "TECH_WARMUP_DAYS_5M": (10, 95),
    "TECH_WARMUP_DAYS_30M": (30, 190),
    "TECH_WARMUP_DAYS_1D": (250, 1900),
    "TECH_STALE_TICK_SECONDS": (5, 120),
    "TECH_FEED_BACKOFF_MAX_SECONDS": (10, 300),
    "TECH_CLOCK_DRIFT_SECONDS": (1, 60),
    "TECH_ALIGN_W_DAILY": (0.0, 1.0),
    "TECH_ALIGN_W_30M": (0.0, 1.0),
    "TECH_ALIGN_W_5M": (0.0, 1.0),
    "TECH_ALIGN_W_1M": (0.0, 1.0),
    "TECH_TREND_ADX_MIN": (10, 35),
    "TECH_TREND_ADX_STRONG": (15, 50),
    "TECH_REGIME_ATR_PCT_HIGH": (60, 99),
    "TECH_REGIME_ATR_PCT_LOW": (1, 40),
    "TECH_BB_COMPRESSION_PCT": (5, 40),
    "TECH_PRESIGNAL_TTL_BARS": (2, 24),
    "TECH_PRESIGNAL_DECAY": (0.5, 1.0),
    "TECH_PRESIGNAL_MIN_CONF": (0.1, 0.9),
    "TECH_LEVEL_PROXIMITY_ATR": (0.1, 2.0),
    "TECH_TELEGRAM_MAX_ALERTS_PER_HOUR": (1, 60),
}

# Settings the tuner must never change, with the value they must keep.
# DRY_RUN is the important one: the whole premise of letting an agent tune
# and deploy unattended is that it's paper money. If this bot ever goes
# live, that decision must be a deliberate human one, and the autonomous
# tuner should be turned off at the same time.
PINNED = {
    "DRY_RUN": "true",
    # second bot: SHADOW until a human decides otherwise (spec §50/§83)
    "TECH_DRY_RUN": "true",
    "TECH_LIVE_TRADING_ENABLED": "false",
}

REQUIRED_PRESENT = ["ENABLE_TRADING", "WATCHLIST", "ENTRY_TIME", "EXIT_TIME", "TECH_MODE", "TECH_UNDERLYINGS"]

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _load_config_env() -> dict[str, str]:
    values = {}
    for line in CONFIG_ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


CONFIG = _load_config_env()


@pytest.mark.parametrize("name,bounds", sorted(NUMERIC_BOUNDS.items()))
def test_numeric_setting_within_bounds(name, bounds):
    if name not in CONFIG:
        pytest.skip(f"{name} not set in config.env - falls back to config.py's default")
    low, high = bounds
    value = float(CONFIG[name])
    assert low <= value <= high, (
        f"{name}={value} is outside the allowed range [{low}, {high}]. "
        f"If this change is genuinely wanted, widen the bound in tests/test_config_bounds.py "
        f"as a reviewed human commit - the nightly tuner must not ship this."
    )


@pytest.mark.parametrize("name,expected", sorted(PINNED.items()))
def test_pinned_setting_unchanged(name, expected):
    actual = CONFIG.get(name)
    assert actual == expected, (
        f"{name} must stay '{expected}' (found '{actual}'). This is a pinned safety setting - "
        f"changing it is a deliberate human decision, not something the nightly tuner may do."
    )


@pytest.mark.parametrize("name", REQUIRED_PRESENT)
def test_required_setting_present(name):
    assert name in CONFIG and CONFIG[name] != "", f"{name} is missing or empty in config.env"


@pytest.mark.parametrize("name", ["ENTRY_TIME", "EXIT_TIME", "SCALP_ORB_REF_START", "SCALP_ORB_REF_END",
                                  "TECH_EOD_CUTOFF", "TECH_SESSION_END", "TECH_EOD_SUMMARY_TIME"])
def test_time_settings_are_valid_hhmm(name):
    if name not in CONFIG:
        pytest.skip(f"{name} not set in config.env")
    assert TIME_RE.match(CONFIG[name]), f"{name}={CONFIG[name]} is not a valid HH:MM 24-hour time"


def test_entry_time_is_before_exit_time():
    assert CONFIG["ENTRY_TIME"] < CONFIG["EXIT_TIME"], (
        f"ENTRY_TIME ({CONFIG['ENTRY_TIME']}) must be before EXIT_TIME ({CONFIG['EXIT_TIME']}) - "
        f"the entry window is `entry_time <= now < exit_time`, so an inverted pair means the bot "
        f"silently never trades."
    )



def test_daily_loss_cap_versus_single_trade_risk_is_not_extreme():
    """A long option's max loss IS the premium paid, so RISK_PER_TRADE_PCT is
    also the worst case for one trade.

    As of writing, live config has per-trade risk (0.12) ABOVE the daily cap
    (0.05) - meaning one full loss halts entries for the rest of that day.
    That's a known, deliberate-for-now state (flagged to the user, not yet
    changed), so this doesn't fail the build. What it does catch is the
    unattended tuner making that ratio dramatically worse - e.g. pushing
    per-trade risk to many times the daily cap, where the bot would halt
    after a fraction of a single losing trade."""
    risk = float(CONFIG["RISK_PER_TRADE_PCT"])
    cap = float(CONFIG["DAILY_LOSS_CAP_PCT"])
    assert risk <= cap * 4, (
        f"RISK_PER_TRADE_PCT ({risk}) is more than 4x DAILY_LOSS_CAP_PCT ({cap}) - at that ratio the "
        f"daily cap halts trading almost immediately and the two settings are fighting each other. "
        f"Reconcile them deliberately rather than letting the tuner drift here."
    )


# --- second bot invariants ---------------------------------------------------


def test_tech_mode_is_a_known_mode_and_not_live():
    """M1 has no execution layer; LIVE needs M4's gates (spec §79/§83).
    Moving this to LIVE is a reviewed human commit that also turns the
    nightly tuner off."""
    assert CONFIG["TECH_MODE"] in ("BACKTEST", "RESEARCH", "PAPER", "SHADOW"), CONFIG["TECH_MODE"]


def test_tech_alignment_weights_sum_to_one():
    total = sum(float(CONFIG[k]) for k in ("TECH_ALIGN_W_DAILY", "TECH_ALIGN_W_30M", "TECH_ALIGN_W_5M", "TECH_ALIGN_W_1M"))
    assert abs(total - 1.0) < 1e-6, f"TECH_ALIGN_W_* must sum to 1.0, got {total}"


def test_tech_session_times_are_ordered():
    assert CONFIG["TECH_EOD_CUTOFF"] < CONFIG["TECH_SESSION_END"] <= CONFIG["TECH_EOD_SUMMARY_TIME"]


def test_tech_adx_thresholds_ordered():
    assert float(CONFIG["TECH_TREND_ADX_MIN"]) < float(CONFIG["TECH_TREND_ADX_STRONG"])
    assert float(CONFIG["TECH_REGIME_ATR_PCT_LOW"]) < float(CONFIG["TECH_REGIME_ATR_PCT_HIGH"])


def test_tech_underlyings_are_the_three_indices_only():
    """Spec scope: NIFTY/BANKNIFTY/SENSEX index options only."""
    names = {s.strip() for s in CONFIG["TECH_UNDERLYINGS"].split(",") if s.strip()}
    assert names and names <= {"NIFTY", "BANKNIFTY", "SENSEX"}, names
