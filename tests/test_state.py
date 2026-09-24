"""Paper-trading state: the capital ledger and the shared trade log."""
def test_reconcile_capital_ignores_the_decommissioned_bots_trades(tmp_path, monkeypatch, caplog):
    """2026-09-24: summing the whole trade log against this bot's ledger showed a
    Rs.5,980 "error" that did not exist - 34 of the 54 records belong to the bot
    decommissioned on 2026-09-16, which kept its own (archived) ledger. Only
    untagged / "main" records count against capital.json."""
    import json as _json
    import logging
    from trading_bot import state as st

    log_path = tmp_path / "trade_log.jsonl"
    rows = [
        {"strategy": "technical_scalp", "realized_pnl": -5354.0},
        {"strategy": "technical_intraday", "realized_pnl": -455.0},
        {"strategy": "technical_swing_equity", "realized_pnl": -451.0},
        {"strategy": "technical_swing_option", "realized_pnl": 281.0},
        {"realized_pnl": 4000.0},                 # this bot: no tag
        {"strategy": "main", "realized_pnl": 3060.0},
    ]
    log_path.write_text(chr(10).join(_json.dumps(r) for r in rows) + chr(10), encoding="utf-8")
    monkeypatch.setattr(st, "TRADE_LOG_PATH", log_path)

    assert st.own_realized_pnl() == (7060.0, 2)
    ledger = {"starting_capital": 50000.0, "current_capital": 57060.0}
    with caplog.at_level(logging.INFO, logger="trading_bot.state"):
        assert st.reconcile_capital(ledger) == 0.0
    assert "reconciles" in caplog.text

    caplog.clear()
    drifted = {"starting_capital": 50000.0, "current_capital": 51000.0}
    with caplog.at_level(logging.WARNING, logger="trading_bot.state"):
        assert st.reconcile_capital(drifted) == -6060.0
    assert "does NOT reconcile" in caplog.text
    assert drifted["current_capital"] == 51000.0  # never rewrites the ledger


def test_own_realized_pnl_survives_a_corrupt_line(tmp_path, monkeypatch):
    import json as _json
    from trading_bot import state as st
    p = tmp_path / "trade_log.jsonl"
    p.write_text(_json.dumps({"realized_pnl": 10.0}) + chr(10) + "not json" + chr(10)
                 + _json.dumps({"realized_pnl": 5.0}) + chr(10), encoding="utf-8")
    monkeypatch.setattr(st, "TRADE_LOG_PATH", p)
    assert st.own_realized_pnl() == (15.0, 2)
