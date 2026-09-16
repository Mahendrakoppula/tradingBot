"""Re-export shim. The regime logic was promoted to
trading_bot/engine/regime.py (the live bot ships only trading_bot/).
Research code and tests keep importing `Regime`/`classify_regime` from
here unchanged; the spec's 17-label `MarketRegime` classifier lives
alongside it in the engine module."""
from trading_bot.engine.regime import Regime, classify_regime  # noqa: F401
