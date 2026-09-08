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
}

# Settings the tuner must never change, with the value they must keep.
# DRY_RUN is the important one: the whole premise of letting an agent tune
# and deploy unattended is that it's paper money. If this bot ever goes
# live, that decision must be a deliberate human one, and the autonomous
# tuner should be turned off at the same time.
PINNED = {
    "DRY_RUN": "true",
}

REQUIRED_PRESENT = ["ENABLE_TRADING", "WATCHLIST", "ENTRY_TIME", "EXIT_TIME"]

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


@pytest.mark.parametrize("name", ["ENTRY_TIME", "EXIT_TIME", "SCALP_ORB_REF_START", "SCALP_ORB_REF_END"])
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
