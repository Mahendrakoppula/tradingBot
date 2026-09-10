import datetime as dt
from dataclasses import dataclass


@dataclass
class Trade:
    strategy_name: str
    direction: str  # "CE" | "PE"
    entry_index: int
    entry_timestamp: object
    entry_spot: float
    entry_premium: float
    strike: float
    expiry: dt.date
    stop_price: float  # in SPOT-price terms, not premium terms
    target_price: float
    exit_index: int | None = None
    exit_timestamp: object | None = None
    exit_spot: float | None = None
    exit_premium: float | None = None
    exit_reason: str | None = None  # "stop" | "target" | "end_of_data"
    pnl: float | None = None  # exit_premium - entry_premium, one conceptual unit (not lot-multiplied)

    @property
    def is_open(self) -> bool:
        return self.exit_index is None
