import json
import tempfile
from pathlib import Path

import trading_bot.state as state_mod
from trading_bot.risk import DailyRiskTracker


def test_risk_tracker_caps_are_capital_relative():
    ledger = {"current_capital": 50000.0}
    risk = DailyRiskTracker(risk_per_trade_pct=0.02, daily_loss_cap_pct=0.05, ledger=ledger)
    assert risk.max_loss_per_trade() == 1000.0
    assert risk.daily_loss_cap() == 2500.0


def test_risk_tracker_blocks_after_daily_cap_breached():
    ledger = {"current_capital": 50000.0}
    risk = DailyRiskTracker(risk_per_trade_pct=0.02, daily_loss_cap_pct=0.05, ledger=ledger)
    risk.record_realized(-1200)
    assert risk.can_enter_new_trade() is True
    risk.record_realized(-1500)  # total -2700, past -2500 cap
    assert risk.can_enter_new_trade() is False


def test_risk_tracker_reset_day_clears_pnl_and_notification_flag():
    ledger = {"current_capital": 50000.0}
    risk = DailyRiskTracker(risk_per_trade_pct=0.02, daily_loss_cap_pct=0.05, ledger=ledger)
    risk.record_realized(-3000)
    risk.cap_notified_today = True
    risk.reset_day()
    assert risk.can_enter_new_trade() is True
    assert risk.cap_notified_today is False


def test_risk_tracker_stop_loss_threshold():
    ledger = {"current_capital": 50000.0}
    risk = DailyRiskTracker(risk_per_trade_pct=0.02, daily_loss_cap_pct=0.05, ledger=ledger)
    # max_loss_per_trade() = 50000 * 0.02 = 1000
    assert risk.should_exit_for_stop(-1500) is True
    assert risk.should_exit_for_stop(-1000) is True
    assert risk.should_exit_for_stop(-500) is False


def test_capital_ledger_persists_across_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "CAPITAL_PATH", tmp_path / "capital.json")
    ledger = state_mod.load_capital(50000.0)
    assert ledger["current_capital"] == 50000.0

    ledger["current_capital"] -= 2700
    state_mod.save_capital(ledger)

    reloaded = state_mod.load_capital(50000.0)  # starting_capital ignored on reload
    assert reloaded["current_capital"] == 47300.0


def test_trade_log_appends_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TRADE_LOG_PATH", tmp_path / "trade_log.jsonl")
    state_mod.log_trade({"underlying": "NIFTY", "realized_pnl": -1200})
    state_mod.log_trade({"underlying": "NIFTY", "realized_pnl": -1500})
    lines = state_mod.TRADE_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["realized_pnl"] == -1200


def test_long_option_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "LONG_STATE_PATH", tmp_path / "long_positions.json")
    leg = state_mod.LegFill(
        tradingsymbol="NIFTY08SEP2625500CE", symboltoken="1", exchange="NFO",
        lotsize=65, freeze_qty=1801, transaction_type="SELL", quantity=65, entry_price=120.5,
    )
    position = state_mod.OpenLongOption(underlying="NIFTY", expiry="08SEP2026", entered_at="2026-09-07T12:30:00", option=leg)
    state_mod.save_long({"NIFTY": position})
    loaded = state_mod.load_long()
    assert loaded["NIFTY"].option.tradingsymbol == "NIFTY08SEP2625500CE"
    assert loaded["NIFTY"].option.entry_price == 120.5


def test_scalp_option_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "SCALP_STATE_PATH", tmp_path / "scalp_positions.json")
    leg = state_mod.LegFill(
        tradingsymbol="NIFTY08SEP2625500CE", symboltoken="1", exchange="NFO",
        lotsize=65, freeze_qty=1801, transaction_type="BUY", quantity=65, entry_price=120.5,
    )
    position = state_mod.OpenScalpOption(
        underlying="NIFTY", expiry="08SEP2026", entered_at="2026-09-07T12:30:00",
        signal_reason="opening-range breakout -> CE", option=leg,
    )
    state_mod.save_scalp({"NIFTY": position})
    loaded = state_mod.load_scalp()
    assert loaded["NIFTY"].option.tradingsymbol == "NIFTY08SEP2625500CE"
    assert loaded["NIFTY"].signal_reason == "opening-range breakout -> CE"


def test_scalp_positions_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "SCALP_STATE_PATH", tmp_path / "scalp_positions.json")
    assert state_mod.load_scalp() == {}


def test_count_scalp_trades_today_only_counts_scalp_strategy_and_today(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TRADE_LOG_PATH", tmp_path / "trade_log.jsonl")
    state_mod.log_trade({"strategy": "scalp", "underlying": "NIFTY", "closed_at": "2026-09-08T12:35:00"})
    state_mod.log_trade({"strategy": "scalp", "underlying": "NIFTY", "closed_at": "2026-09-08T12:50:00"})
    state_mod.log_trade({"strategy": "scalp", "underlying": "BANKNIFTY", "closed_at": "2026-09-08T13:00:00"})
    state_mod.log_trade({"strategy": "scalp", "underlying": "NIFTY", "closed_at": "2026-09-07T12:35:00"})  # yesterday
    state_mod.log_trade({"underlying": "NIFTY", "closed_at": "2026-09-08T15:15:00"})  # daily strategy, no tag

    counts = state_mod.count_scalp_trades_today("2026-09-08")
    assert counts == {"NIFTY": 2, "BANKNIFTY": 1}
