import datetime as dt
from unittest.mock import patch

import trading_bot.run_technical as rt
import trading_bot.state as state_mod


def _cfg(dry_run=True):
    from trading_bot.technical_config import TechnicalConfig
    return TechnicalConfig(dry_run=dry_run, enable_trading=False)


def test_send_eod_trade_summary_no_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TRADE_LOG_PATH", tmp_path / "trade_log.jsonl")
    ledger = {"current_capital": 50000.0}
    journal = {"starting_capital": 50000.0}
    with patch.object(rt, "notify") as mock_notify:
        rt._send_eod_trade_summary(_cfg(), ledger, dt.date(2026, 9, 10), journal)
    mock_notify.assert_called_once()
    (message,), kwargs = mock_notify.call_args
    assert "No trades today" in message
    assert kwargs.get("html") is True


def test_send_eod_trade_summary_lists_each_trade_and_totals(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TRADE_LOG_PATH", tmp_path / "trade_log.jsonl")
    state_mod.log_trade({
        "strategy": "technical_scalp", "underlying": "NIFTY",
        "entered_at": "2026-09-10T09:50:19+05:30", "closed_at": "2026-09-10T10:02:10+05:30",
        "realized_pnl": -363.35, "costs": 66.51, "capital_after": 49788.60,
    })
    state_mod.log_trade({
        "strategy": "technical_intraday", "underlying": "MARUTI",
        "entered_at": "2026-09-10T15:07:14+05:30", "closed_at": "2026-09-10T15:15:44+05:30",
        "realized_pnl": 352.00, "costs": 12.30, "capital_after": 47928.60,
    })
    state_mod.log_trade({  # daily bot's own record, no "strategy" tag - must be excluded
        "underlying": "NIFTY", "entered_at": "2026-09-10T10:09:05+05:30", "closed_at": "2026-09-10T15:15:16+05:30",
        "realized_pnl": -52.00, "capital_after": 50176.50,
    })
    ledger = {"current_capital": 47928.60}
    journal = {"starting_capital": 47928.60 - (-363.35 + 352.00)}  # day-start capital before today's technical trades

    with patch.object(rt, "notify") as mock_notify:
        rt._send_eod_trade_summary(_cfg(), ledger, dt.date(2026, 9, 10), journal)

    mock_notify.assert_called_once()
    (message,), kwargs = mock_notify.call_args
    assert kwargs.get("html") is True
    assert "NIFTY" in message and "09:50:19" in message and "10:02:10" in message
    assert "MARUTI" in message and "15:07:14" in message and "15:15:44" in message
    assert "-363.35" in message
    assert "352.00" in message
    assert "2 trades" in message
    assert "1W/1L" in message
    assert "-11.35" in message  # net P&L: -363.35 + 352.00
    assert "[SCALP]" in message
    assert "[INTRADAY]" in message


def test_tier_label_covers_every_technical_strategy_tag():
    assert rt._tier_label("technical_scalp") == "SCALP"
    assert rt._tier_label("technical_intraday") == "INTRADAY"
    assert rt._tier_label("technical_swing_option") == "SWING (option)"
    assert rt._tier_label("technical_swing_equity") == "SWING (equity)"
    assert rt._tier_label("something_unexpected") == "something_unexpected"  # falls back to the raw tag, never blank
    assert rt._tier_label("") == "?"


# --- _format_entry_time: swing positions can be entered days before they
# close, unlike scalp/intraday which always open and close same-day ---


def test_format_entry_time_same_day_is_time_only():
    assert rt._format_entry_time("2026-09-10T09:50:19+05:30", dt.date(2026, 9, 10)) == "09:50:19"


def test_format_entry_time_earlier_day_includes_date():
    assert rt._format_entry_time("2026-09-08T10:54:16+05:30", dt.date(2026, 9, 10)) == "08-Sep 10:54:16"


def test_format_entry_time_falls_back_to_raw_string_on_bad_input():
    assert rt._format_entry_time("not-a-timestamp", dt.date(2026, 9, 10)) == "not-a-timestamp"
    assert rt._format_entry_time("", dt.date(2026, 9, 10)) == "unknown"


def test_send_eod_trade_summary_shows_date_for_a_multi_day_swing_trade(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TRADE_LOG_PATH", tmp_path / "trade_log.jsonl")
    state_mod.log_trade({
        "strategy": "technical_swing_equity", "underlying": "DLF",
        "entered_at": "2026-09-08T10:54:16+05:30", "closed_at": "2026-09-10T09:26:08+05:30",
        "realized_pnl": -49.05, "costs": 5.00, "capital_after": 49950.95,
    })
    ledger = {"current_capital": 49950.95}
    journal = {"starting_capital": 49950.95 - (-49.05)}

    with patch.object(rt, "notify") as mock_notify:
        rt._send_eod_trade_summary(_cfg(), ledger, dt.date(2026, 9, 10), journal)

    (message,), _ = mock_notify.call_args
    assert "[SWING (equity)]" in message
    assert "08-Sep 10:54:16" in message  # entry date shown - it wasn't opened today
    assert "09:26:08" in message  # exit is today, time-only is enough
