"""Centralized, validated configuration - Section 71/72 of the master
spec. Pydantic (not hand-rolled os.environ.get() chains like the sibling
trading_bot/technical_config.py on `main` uses) so a missing/malformed
required value fails LOUDLY at startup with a clear message, rather than
silently defaulting or crashing deep inside trading logic later. Codex is
a separate stack from `main`'s trading_bot/ package on purpose (see
README.md) - this is a deliberate divergence from that project's own
config convention, not an oversight.

IMPORTANT (Section 72 of the spec): PROFIT_PROTECTION_LEVEL and
PROFIT_SELECTIVITY_LEVEL are DAILY P&L BEHAVIOR THRESHOLDS ("become more
selective once this much is banked today"), never an individual trade's
stop-loss or target. Per-trade stops/targets are computed dynamically
from market structure/volatility in a later phase - nothing in this
settings module defines a fixed per-trade rupee stop, deliberately.
"""
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- capital & daily risk policy (Section 2, 35, 72) ---
    capital: float = Field(default=50_000.0, gt=0, description="Starting trading capital in rupees")
    max_daily_loss: float = Field(default=2_000.0, gt=0, description="Hard, non-overridable daily loss kill switch")
    profit_protection_level: float = Field(
        default=800.0, gt=0,
        description="Daily P&L level past which the system becomes more selective - NOT a per-trade target",
    )
    profit_selectivity_level: float = Field(
        default=1_000.0, gt=0,
        description="Daily P&L level past which only the highest-quality setups are taken - NOT a stop point",
    )

    @field_validator("profit_selectivity_level")
    @classmethod
    def _selectivity_at_or_above_protection(cls, v: float, info) -> float:
        protection = info.data.get("profit_protection_level")
        if protection is not None and v < protection:
            raise ValueError(
                f"profit_selectivity_level ({v}) must be >= profit_protection_level ({protection}) - "
                "selectivity is meant to tighten further past the protection level, not loosen it."
            )
        return v

    @field_validator("max_daily_loss")
    @classmethod
    def _daily_loss_not_absurd_relative_to_capital(cls, v: float, info) -> float:
        capital = info.data.get("capital")
        if capital is not None and v > capital:
            raise ValueError(f"max_daily_loss ({v}) cannot exceed starting capital ({capital}).")
        return v

    # --- instrument scope (Section 1) ---
    instruments: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")

    # --- environment/deployment ---
    environment: str = Field(default="dry_run", description="'dry_run' | 'paper' | 'live' - see Section 58-60's gated rollout")
    dry_run: bool = Field(default=True, description="No orders placed, no live data commitments required yet (Phase 1)")

    # --- database (self-hosted TimescaleDB per the plan's infra section) ---
    database_url: str = Field(
        default="postgresql://codex:codex@localhost:5432/codex",
        description="TimescaleDB (Postgres + Timescale extension) connection string",
    )

    # --- Telegram notifications - same shape as trading_bot/notifier.py on
    # `main`, deliberately reusing that proven pattern, but its own bot/
    # channel (CODEX_ prefix distinguishes it in the shared .env). Empty by
    # default - notifier.py's own best-effort behavior handles that, same
    # as the `main`-branch bots' own notifiers do. ---
    codex_telegram_bot_token: str = Field(default="", description="Codex's own Telegram bot token - not yet provided")
    codex_telegram_chat_id: str = Field(default="", description="Codex's own Telegram chat ID - not yet provided")

    # --- broker credentials (reused from the same Angel One SmartAPI
    # account the `main`-branch bots use - one API key per ACCOUNT, not
    # per app, confirmed on that project) ---
    smartapi_key: str = Field(default="", description="Angel One SmartAPI key")
    smartapi_client_code: str = Field(default="")
    smartapi_pin: str = Field(default="")
    smartapi_totp_secret: str = Field(default="")


def get_settings() -> Settings:
    """Fresh Settings() each call rather than a cached singleton - cheap to
    construct, and avoids a stale-config surprise in tests that monkeypatch
    environment variables between cases."""
    return Settings()
