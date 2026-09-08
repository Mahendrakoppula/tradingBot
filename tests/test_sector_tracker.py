from trading_bot.sector_tracker import (
    allows_direction,
    format_sector_snapshot,
    get_sector_snapshot,
    resolve_sector_rows,
)


class FakeRest:
    def __init__(self, quotes):
        self.quotes = quotes

    def get_quote(self, mode, exchange_tokens):
        return {"fetched": self.quotes}


def _row(label, name, token):
    return {"exch_seg": "NSE", "instrumenttype": "AMXIDX", "name": name, "symbol": label, "token": token}


def test_resolve_sector_rows_skips_missing_and_matches_by_name():
    instruments = [
        _row("Nifty IT", "NIFTY IT", "99926008"),
        _row("Nifty Auto", "NIFTY AUTO", "99926029"),
    ]
    rows = resolve_sector_rows(instruments)
    assert set(rows.keys()) == {"IT", "Auto"}
    assert rows["IT"]["token"] == "99926008"


def test_get_sector_snapshot_computes_breadth_and_verdicts():
    sector_rows = {
        "PSU Bank": {"token": "1"},
        "Pvt Bank": {"token": "2"},
        "IT": {"token": "3"},
        "Auto": {"token": "4"},
        "Metal": {"token": "5"},
    }
    quotes = [
        {"symbolToken": "1", "percentChange": "-1.0"},
        {"symbolToken": "2", "percentChange": "-0.8"},
        {"symbolToken": "3", "percentChange": "0.9"},
        {"symbolToken": "4", "percentChange": "0.7"},
        {"symbolToken": "5", "percentChange": "0.6"},
    ]
    rest = FakeRest(quotes)
    snap = get_sector_snapshot(rest, sector_rows, move_threshold_pct=0.3)

    assert snap["advancing"] == 3
    assert snap["declining"] == 2
    assert snap["sectors"][0]["label"] == "IT"  # sorted best -> worst
    assert snap["underlying_verdict"]["NIFTY"] == "BULLISH"  # 3/5 advancing = 60%, hits the >=60% bar
    assert snap["underlying_verdict"]["BANKNIFTY"] == "BEARISH"  # avg(PSU, Pvt) = -0.9%


def test_allows_direction_blocks_conflicting_and_passes_agreeing():
    snapshot = {"underlying_verdict": {"BANKNIFTY": "BEARISH", "NIFTY": "NEUTRAL"}}
    assert allows_direction("BANKNIFTY", "PE", snapshot) is True
    assert allows_direction("BANKNIFTY", "CE", snapshot) is False
    assert allows_direction("NIFTY", "CE", snapshot) is True
    assert allows_direction("NIFTY", "PE", snapshot) is True


def test_allows_direction_unmapped_underlying_is_unrestricted():
    snapshot = {"underlying_verdict": {"BANKNIFTY": "BULLISH"}}
    assert allows_direction("RELIANCE", "PE", snapshot) is True


def test_format_sector_snapshot_includes_gate_line():
    snapshot = {
        "sectors": [{"label": "IT", "pct_change": 0.9}, {"label": "Metal", "pct_change": -0.4}],
        "advancing": 1,
        "declining": 1,
        "underlying_verdict": {"NIFTY": "NEUTRAL", "BANKNIFTY": "BULLISH"},
    }
    text = format_sector_snapshot(snapshot)
    assert "IT +0.90%" in text
    assert "Metal -0.40%" in text
    assert "BANKNIFTY: BULLISH" in text
