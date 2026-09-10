"""Daily P&L-based selectivity engine (spec: configurable selectivity/
risk-reduction tiers down to a HARD STOP at the max daily loss). Two
independent axes, per settings' own field docs - never conflate them:

  - LOSS side: max_daily_loss (default Rs.2,000) is a hard,
    non-overridable kill switch - once today's realized P&L breaches it,
    no new trades for the rest of the day, full stop.
  - PROFIT side: profit_protection_level/profit_selectivity_level
    (default Rs.800/Rs.1,000) are NOT stop points and NOT daily profit
    targets to chase - they raise the confidence bar required for a new
    trade once that much has already been banked today, protecting
    gains already made rather than risking them for marginal additional
    upside on a lower-quality setup.
"""
from dataclasses import dataclass, field

MIN_CONFIDENCE_NORMAL = 0.50
MIN_CONFIDENCE_AT_PROTECTION_LEVEL = 0.70
MIN_CONFIDENCE_AT_SELECTIVITY_LEVEL = 0.85


@dataclass
class RiskDecision:
    allow_new_trades: bool
    min_confidence_required: float
    reason: str


@dataclass
class DailyRiskEngine:
    max_daily_loss: float
    profit_protection_level: float
    profit_selectivity_level: float
    _daily_pnl: float = field(default=0.0, init=False)

    def record_realized_pnl(self, pnl: float) -> None:
        self._daily_pnl += pnl

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    def reset_day(self) -> None:
        self._daily_pnl = 0.0

    def evaluate(self) -> RiskDecision:
        pnl = self._daily_pnl

        if pnl <= -abs(self.max_daily_loss):
            return RiskDecision(
                allow_new_trades=False, min_confidence_required=1.0,
                reason=f"Hard daily loss limit reached ({pnl:.2f} <= -{self.max_daily_loss:.2f}) - no new trades today",
            )
        if pnl >= self.profit_selectivity_level:
            return RiskDecision(
                allow_new_trades=True, min_confidence_required=MIN_CONFIDENCE_AT_SELECTIVITY_LEVEL,
                reason=f"Daily profit {pnl:.2f} >= selectivity level {self.profit_selectivity_level:.2f} - only the highest-quality setups now",
            )
        if pnl >= self.profit_protection_level:
            return RiskDecision(
                allow_new_trades=True, min_confidence_required=MIN_CONFIDENCE_AT_PROTECTION_LEVEL,
                reason=f"Daily profit {pnl:.2f} >= protection level {self.profit_protection_level:.2f} - raising the selectivity bar to protect it",
            )
        return RiskDecision(
            allow_new_trades=True, min_confidence_required=MIN_CONFIDENCE_NORMAL,
            reason="Normal trading - no selectivity adjustment",
        )
