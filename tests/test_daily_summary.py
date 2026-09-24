"""Deterministic daily-bot summary posted with the nightly engine review."""
import datetime as dt

from trading_bot.daily_summary import summarise


def _t(u, typ, lots, pnl, day, reason="exit_time", strategy=None):
    r = {"underlying": u, "option_type": typ, "qty_lots": lots, "realized_pnl": pnl, "reason": reason,
         "entered_at": f"{day}T09:20:00+05:30", "closed_at": f"{day}T15:15:00+05:30"}
    if strategy:
        r["strategy"] = strategy
    return r


def test_summary_reports_todays_trades_the_record_and_the_concentration():
    rows = [
        _t("BANKNIFTY", "PE", 1, 7214.0, "2026-09-15"),
        _t("NIFTY", "CE", 5, -1934.0, "2026-09-16"),
        _t("BANKNIFTY", "CE", 2, -1794.0, "2026-09-22", reason="stop_loss"),
        _t("NIFTY", "PE", 5, 4530.0, "2026-09-24"),
    ]
    text = summarise(dt.date(2026, 9, 24), rows, {"starting_capital": 50000.0, "current_capital": 58016.0})
    assert "today: 1 trade(s), net +4,530" in text
    assert "NIFTY PE x5" in text and "(exit_time)" in text
    assert "all time: 4 trades, net +8,016, win 2/4 = 50%" in text
    # the honesty line: two trades carry the whole result
    assert "top 2 trades +11,744 of +8,016; the other 2 sum -3,728" in text
    assert "by exit: exit_time=" in text and "stop_loss=-1,794(n1)" in text
    assert "reconciles" in text and "DOES NOT" not in text
    assert "not tuned on this sample" in text


def test_summary_excludes_the_decommissioned_bots_trades_and_flags_a_bad_ledger():
    rows = [_t("NIFTY", "PE", 5, 1000.0, "2026-09-24"),
            _t("NIFTY", "CE", 5, -9999.0, "2026-09-24", strategy="technical_scalp")]
    # the technical_* row is filtered out by _rows(); summarise() is given pre-filtered rows here,
    # so assert the filter itself where it lives
    from trading_bot import state as st
    assert st.OWN_STRATEGY_TAGS == (None, "main")
    text = summarise(dt.date(2026, 9, 24), rows[:1], {"starting_capital": 50000.0, "current_capital": 57000.0})
    assert "all time: 1 trades, net +1,000" in text
    assert "DOES NOT RECONCILE" in text and "+6,000" in text


def test_summary_handles_a_day_with_no_trades_and_an_empty_log():
    rows = [_t("NIFTY", "PE", 5, 500.0, "2026-09-23")]
    assert "today: no trades" in summarise(dt.date(2026, 9, 24), rows)
    assert "no trades logged yet" in summarise(dt.date(2026, 9, 24), [])
