"""Pure data-preparation functions for the dashboard (spec Phase 15) -
testable without Streamlit itself. dashboard/app.py is a thin rendering
layer that only calls these; none of the actual logic lives there,
specifically so it stays unit-testable (a rendered Streamlit page isn't
meaningfully testable without a browser).

Named queries.py, not data.py - a same-named module.py sitting directly
inside a package that Streamlit runs as a script collides with the
top-level data/ package once Streamlit puts this directory on sys.path
(confirmed live: caused a real "'data' is not a package" ImportError
the first time this was run in a browser, not just imported by pytest -
exactly why this project insists on actually testing UI in a browser).
"""
import datetime as dt
from dataclasses import dataclass

import pandas as pd

from data.quality import check_ohlcv
from data.storage import load_ohlcv
from market_state.classifier import MarketState, classify_market_state
from models.feature_engineering import batch_regime_labels

INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"]
INTERVALS = ["ONE_MINUTE", "FIVE_MINUTE", "TEN_MINUTE", "THIRTY_MINUTE", "ONE_HOUR", "ONE_DAY"]


@dataclass
class DataHealthRow:
    instrument: str
    interval: str
    n_rows: int
    last_timestamp: object | None
    days_since_last: int | None
    quality_errors: int
    quality_warnings: int


def data_health_report(as_of: dt.date | None = None) -> list[DataHealthRow]:
    as_of = as_of or dt.date.today()
    rows = []
    for instrument in INSTRUMENTS:
        for interval in INTERVALS:
            df = load_ohlcv(instrument, interval)
            if len(df) == 0:
                rows.append(DataHealthRow(instrument, interval, 0, None, None, 0, 0))
                continue
            last_ts = df["timestamp"].iloc[-1]
            last_date = last_ts.date() if hasattr(last_ts, "date") else last_ts
            report = check_ohlcv(df.to_dict("records"), instrument, interval)
            rows.append(DataHealthRow(
                instrument=instrument, interval=interval, n_rows=len(df), last_timestamp=last_ts,
                days_since_last=(as_of - last_date).days,
                quality_errors=len(report.errors), quality_warnings=len(report.warnings),
            ))
    return rows


def current_market_state(instrument: str, interval: str) -> MarketState | None:
    df = load_ohlcv(instrument, interval)
    if len(df) == 0:
        return None
    return classify_market_state(df)


def regime_history(instrument: str, interval: str) -> pd.DataFrame:
    """One row per bar: timestamp + regime label, for a timeline view.
    Uses the vectorized batch computation (models/feature_engineering.py),
    NOT a per-bar classify_market_state() call, which would be quadratic
    on a long series."""
    df = load_ohlcv(instrument, interval)
    if len(df) == 0:
        return pd.DataFrame(columns=["timestamp", "regime"])
    labels = batch_regime_labels(df)
    return pd.DataFrame({"timestamp": df["timestamp"], "regime": labels})
