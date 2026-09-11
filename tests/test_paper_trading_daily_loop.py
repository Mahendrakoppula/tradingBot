"""requires_real_data tests need data/raw/NIFTY/ONE_DAY.parquet (see
tests/test_feature_engineering.py's module docstring for why)."""
from unittest.mock import patch

import pytest

from data.storage import load_ohlcv
from paper_trading.daily_loop import run_for_instrument
from paper_trading.state import load_state

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")


def test_insufficient_history_returns_empty_state_without_crashing(tmp_path):
    with patch("paper_trading.daily_loop.load_ohlcv", return_value=NIFTY_DAILY.iloc[:5]):
        state = run_for_instrument("NIFTY", state_dir=tmp_path)
    assert state.open_trade is None
    assert state.last_processed_timestamp is None


@requires_real_data
def test_first_run_sets_last_processed_timestamp(tmp_path):
    with patch("paper_trading.daily_loop.notify"):
        state = run_for_instrument("NIFTY", state_dir=tmp_path)
    expected_key = NIFTY_DAILY["timestamp"].iloc[-1].isoformat()
    assert state.last_processed_timestamp == expected_key


@requires_real_data
def test_rerunning_the_same_day_is_idempotent(tmp_path):
    with patch("paper_trading.daily_loop.notify") as mock_notify:
        first = run_for_instrument("NIFTY", state_dir=tmp_path)
        second = run_for_instrument("NIFTY", state_dir=tmp_path)

    assert first.last_processed_timestamp == second.last_processed_timestamp
    assert first.open_trade == second.open_trade
    # notify is only called on the FIRST run (if at all) - the second
    # run must short-circuit before ever reaching a notification branch
    assert mock_notify.call_count <= 1


@requires_real_data
def test_state_persists_to_disk_between_calls(tmp_path):
    with patch("paper_trading.daily_loop.notify"):
        run_for_instrument("NIFTY", state_dir=tmp_path)
    reloaded = load_state("NIFTY", state_dir=tmp_path)
    assert reloaded.last_processed_timestamp is not None


def test_never_imports_the_order_client():
    """Structural guarantee, not just discipline: this module must never
    be able to place a real order, so it must never even import the
    client that can - checked by parsing the AST for actual import
    statements, not a raw text search (which would false-positive on
    the docstring explaining this very constraint)."""
    import ast

    import paper_trading.daily_loop as module

    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any("order_client" in name for name in imported_modules)
