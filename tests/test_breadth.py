from trading_bot.breadth import compute_breadth, resolve_constituents


def test_compute_breadth_counts_and_rankings():
    quotes = [
        {"tradingSymbol": "A-EQ", "percentChange": "2.5", "tradeVolume": "1000000"},
        {"tradingSymbol": "B-EQ", "percentChange": "-1.2", "tradeVolume": "500000"},
        {"tradingSymbol": "C-EQ", "percentChange": "0.0", "tradeVolume": "200000"},
        {"tradingSymbol": "D-EQ", "percentChange": "5.0", "tradeVolume": "3000000"},
        {"tradingSymbol": "E-EQ", "percentChange": "-3.0", "tradeVolume": "100000"},
    ]
    b = compute_breadth(quotes)
    assert b["count"] == 5
    assert b["advancing"] == 2
    assert b["declining"] == 2
    assert b["unchanged"] == 1
    assert b["total_volume"] == 4800000
    assert b["top_gainers"][0][0] == "D-EQ"
    assert b["top_losers"][0][0] == "E-EQ"
    assert b["top_volume"][0][0] == "D-EQ"


def test_resolve_constituents_skips_missing_names_gracefully():
    instruments = [
        {"exch_seg": "NSE", "instrumenttype": "", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "token": "2885"},
        {"exch_seg": "NSE", "instrumenttype": "", "symbol": "TCS-EQ", "name": "TCS", "token": "11536"},
    ]
    rows = resolve_constituents(instruments, ["RELIANCE", "TCS", "NOTAREALSTOCK"])
    assert len(rows) == 2
    assert {r["name"] for r in rows} == {"RELIANCE", "TCS"}
