"""Local Parquet storage for OHLCV history. Local disk only in Phase 1/2
(no S3/TimescaleDB dependency yet - see README.md on why this instance
has neither right now); the storage boundary is isolated in this one
module specifically so a later phase can swap the backend without
touching data/historical.py or anything downstream.
"""
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "raw"

REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def _path_for(symbol: str, interval: str, root: Path = DATA_ROOT) -> Path:
    return root / symbol.upper() / f"{interval}.parquet"


def save_ohlcv(rows: list[dict], symbol: str, interval: str, root: Path = DATA_ROOT) -> Path:
    """Overwrites any existing file for this symbol/interval with the
    given rows, deduplicated and sorted by timestamp. Callers are
    expected to have already run data.quality.check_ohlcv and decided
    the data is acceptable - this function does not re-validate."""
    df = pd.DataFrame(rows, columns=REQUIRED_COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)

    path = _path_for(symbol, interval, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    log.info("saved %d rows -> %s", len(df), path)
    return path


def load_ohlcv(symbol: str, interval: str, root: Path = DATA_ROOT) -> pd.DataFrame:
    path = _path_for(symbol, interval, root)
    if not path.exists():
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    return pd.read_parquet(path)
