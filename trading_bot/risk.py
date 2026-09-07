import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class DailyRiskTracker:
    """Gates new entries once the day's realized P&L breaches the daily loss
    cap, and flags individual positions that have breached their own
    per-trade stop. Thresholds are a percentage of the CURRENT capital
    ledger balance, not a fixed rupee figure - they shrink if paper trading
    loses money and grow if it makes money, same as real risk management
    tied to account equity.

    Does not itself enforce anything - callers must check
    can_enter_new_trade() before opening a position and should_exit_for_stop()
    while monitoring open ones.
    """

    risk_per_trade_pct: float
    daily_loss_cap_pct: float
    ledger: dict  # {"current_capital": float, ...} - shared, mutated by the caller
    _daily_pnl: float = field(default=0.0, init=False)
    cap_notified_today: bool = field(default=False, init=False)  # callers can flip this to avoid repeat alerts

    def max_loss_per_trade(self) -> float:
        return self.ledger["current_capital"] * self.risk_per_trade_pct

    def daily_loss_cap(self) -> float:
        return self.ledger["current_capital"] * self.daily_loss_cap_pct

    def record_realized(self, pnl: float) -> None:
        self._daily_pnl += pnl
        log.info("Realized P&L %.2f, running daily P&L %.2f", pnl, self._daily_pnl)

    def daily_pnl(self) -> float:
        return self._daily_pnl

    def can_enter_new_trade(self) -> bool:
        cap = self.daily_loss_cap()
        if self._daily_pnl <= -abs(cap):
            log.warning(
                "Daily loss cap hit (%.2f <= -%.2f, %.0f%% of Rs.%.2f capital) - blocking new entries",
                self._daily_pnl, cap, self.daily_loss_cap_pct * 100, self.ledger["current_capital"],
            )
            return False
        return True

    def should_exit_for_stop(self, unrealized_pnl: float) -> bool:
        return unrealized_pnl <= -abs(self.max_loss_per_trade())

    def reset_day(self) -> None:
        self._daily_pnl = 0.0
        self.cap_notified_today = False
