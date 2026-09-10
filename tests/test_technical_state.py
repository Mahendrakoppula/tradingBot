import trading_bot.state as state_mod


def _legfill(sym="X", price=10.0, qty=100):
    return state_mod.LegFill(
        tradingsymbol=sym, symboltoken="1", exchange="NFO", lotsize=1, freeze_qty=0,
        transaction_type="BUY", quantity=qty, entry_price=price,
    )


def test_technical_scalp_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TECHNICAL_SCALP_STATE_PATH", tmp_path / "scalp.json")
    pos = state_mod.OpenTechnicalOption(
        underlying="NIFTY", expiry="08SEP2026", entered_at="2026-09-09T09:20:00",
        tier="scalp", signal_reason="golden cross", entry_spot=25000.0,
        stop_price=24950.0, target_price=25100.0, option=_legfill(), stop_orderid="SL1", target_orderid="TP1",
    )
    state_mod.save_technical_scalp({"NIFTY": pos})
    loaded = state_mod.load_technical_scalp()
    assert loaded["NIFTY"].tier == "scalp"
    assert loaded["NIFTY"].entry_spot == 25000.0
    assert loaded["NIFTY"].stop_price == 24950.0
    assert loaded["NIFTY"].target_price == 25100.0
    assert loaded["NIFTY"].stop_orderid == "SL1"
    assert loaded["NIFTY"].option.tradingsymbol == "X"


def test_technical_intraday_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TECHNICAL_INTRADAY_STATE_PATH", tmp_path / "intraday.json")
    pos = state_mod.OpenTechnicalOption(
        underlying="BANKNIFTY", expiry="30SEP2026", entered_at="2026-09-09T10:00:00",
        tier="intraday", signal_reason="pivot breakout", entry_spot=52000.0,
        stop_price=51800.0, target_price=52400.0, option=_legfill(),
    )
    state_mod.save_technical_intraday({"BANKNIFTY": pos})
    loaded = state_mod.load_technical_intraday()
    assert loaded["BANKNIFTY"].tier == "intraday"
    assert loaded["BANKNIFTY"].stop_orderid == ""  # default when never placed (dry-run)


def test_technical_swing_option_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TECHNICAL_SWING_OPTION_STATE_PATH", tmp_path / "swing_option.json")
    pos = state_mod.OpenTechnicalSwingOption(
        underlying="NIFTY", expiry="30OCT2026", entered_at="2026-09-09T09:20:00",
        direction="long", broken_level=25100.0, entry_index_price=25200.0,
        stop_price=24950.0, target_price=25600.0, atr_value=180.0, favorable_extreme=25200.0,
        trailing_active=False, signal_reason="resistance breakout", option=_legfill(),
    )
    state_mod.save_technical_swing_option({"NIFTY": pos})
    loaded = state_mod.load_technical_swing_option()
    assert loaded["NIFTY"].direction == "long"
    assert loaded["NIFTY"].broken_level == 25100.0
    assert loaded["NIFTY"].stop_price == 24950.0


def test_technical_swing_equity_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TECHNICAL_SWING_EQUITY_STATE_PATH", tmp_path / "swing_equity.json")
    pos = state_mod.OpenTechnicalEquity(
        underlying="RELIANCE", entered_at="2026-09-09T09:20:00", broken_level=2900.0,
        stop_price=2870.0, target_price=3010.0, atr_value=25.0, favorable_extreme=2950.0,
        trailing_active=False, signal_reason="resistance breakout",
        equity=_legfill(sym="RELIANCE-EQ", price=2950.0, qty=10),
    )
    state_mod.save_technical_swing_equity({"RELIANCE": pos})
    loaded = state_mod.load_technical_swing_equity()
    assert loaded["RELIANCE"].equity.tradingsymbol == "RELIANCE-EQ"
    assert loaded["RELIANCE"].broken_level == 2900.0
    assert loaded["RELIANCE"].stop_price == 2870.0


def test_technical_capital_ledger_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TECHNICAL_CAPITAL_PATH", tmp_path / "technical_capital.json")
    ledger = state_mod.load_technical_capital(50000.0)
    assert ledger["current_capital"] == 50000.0
    ledger["current_capital"] = 51000.0
    state_mod.save_technical_capital(ledger)
    reloaded = state_mod.load_technical_capital(50000.0)
    assert reloaded["current_capital"] == 51000.0  # ignores starting_capital on a subsequent load


def test_count_trades_today_filters_by_strategy_tag_and_day(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TRADE_LOG_PATH", tmp_path / "trade_log.jsonl")
    state_mod.log_trade({"strategy": "technical_scalp", "underlying": "NIFTY", "closed_at": "2026-09-09T10:00:00"})
    state_mod.log_trade({"strategy": "technical_intraday", "underlying": "NIFTY", "closed_at": "2026-09-09T11:00:00"})
    state_mod.log_trade({"strategy": "technical_scalp", "underlying": "NIFTY", "closed_at": "2026-09-08T10:00:00"})
    counts = state_mod.count_trades_today("technical_scalp", "2026-09-09")
    assert counts == {"NIFTY": 1}


def test_load_technical_trades_today_filters_and_sorts(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TRADE_LOG_PATH", tmp_path / "trade_log.jsonl")
    state_mod.log_trade({"strategy": "technical_intraday", "underlying": "SENSEX", "closed_at": "2026-09-09T14:00:00", "realized_pnl": 5.0})
    state_mod.log_trade({"strategy": "technical_scalp", "underlying": "NIFTY", "closed_at": "2026-09-09T10:00:00", "realized_pnl": -10.0})
    state_mod.log_trade({"underlying": "NIFTY", "closed_at": "2026-09-09T15:15:00", "realized_pnl": 20.0})  # daily bot, no "strategy" tag
    state_mod.log_trade({"strategy": "technical_scalp", "underlying": "BANKNIFTY", "closed_at": "2026-09-08T10:00:00", "realized_pnl": 1.0})
    trades = state_mod.load_technical_trades_today("2026-09-09")
    assert [t["underlying"] for t in trades] == ["NIFTY", "SENSEX"]  # sorted by closed_at, daily bot's un-tagged record excluded
