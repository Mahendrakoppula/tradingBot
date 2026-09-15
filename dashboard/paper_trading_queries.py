"""Paper-trading data-preparation for the dashboard - reads the SAME
persisted state paper_trading/daily_loop.py writes
(paper_trading/state.py), never a separate copy of the logic.

To review the LIVE instance's accumulated history locally, sync
.state/paper_trading/ down first (same S3-sync pattern already used for
data/raw/ - see README.md's "Local development" section) - this module
just reads whatever is on disk, it never fetches anything itself.
"""
from dataclasses import dataclass
from pathlib import Path

from backtesting.attribution import breakdown_by_strategy
from backtesting.metrics import PerformanceSummary, summarize
from backtesting.trade_record import Trade
from paper_trading.daily_loop import INSTRUMENTS
from paper_trading.state import STATE_DIR, load_state


@dataclass
class PaperTradingOverview:
    instrument: str
    open_trade: Trade | None
    last_processed_timestamp: str | None
    completed_trades: list[Trade]
    summary: PerformanceSummary
    by_strategy: dict[str, PerformanceSummary]


def load_paper_trading_overview(instrument: str, state_dir: Path = STATE_DIR) -> PaperTradingOverview:
    state = load_state(instrument, state_dir)
    return PaperTradingOverview(
        instrument=instrument,
        open_trade=state.open_trade,
        last_processed_timestamp=state.last_processed_timestamp,
        completed_trades=state.completed_trades,
        summary=summarize(state.completed_trades),
        by_strategy=breakdown_by_strategy(state.completed_trades),
    )


def load_all_paper_trading_overviews(state_dir: Path = STATE_DIR) -> list[PaperTradingOverview]:
    return [load_paper_trading_overview(instrument, state_dir) for instrument in INSTRUMENTS]
