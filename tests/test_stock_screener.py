from trading_bot.stock_screener import build_universe, filter_by_volume, fo_eligible_stock_names, resolve_spot_rows


def test_fo_eligible_stock_names_filters_optstk_nfo_only():
    instruments = [
        {"name": "RELIANCE", "instrumenttype": "OPTSTK", "exch_seg": "NFO"},
        {"name": "TCS", "instrumenttype": "OPTSTK", "exch_seg": "NFO"},
        {"name": "NIFTY", "instrumenttype": "OPTIDX", "exch_seg": "NFO"},  # index, not a stock
        {"name": "WIPRO", "instrumenttype": "OPTSTK", "exch_seg": "BFO"},  # wrong exchange
    ]
    assert fo_eligible_stock_names(instruments) == ["RELIANCE", "TCS"]


def test_resolve_spot_rows_skips_unresolvable_names():
    instruments = [
        {"name": "RELIANCE", "instrumenttype": "", "exch_seg": "NSE", "symbol": "RELIANCE-EQ", "token": "1"},
    ]
    rows = resolve_spot_rows(instruments, ["RELIANCE", "GHOST"])
    assert len(rows) == 1
    assert rows[0]["symbol"] == "RELIANCE-EQ"


class FakeQuoteRest:
    def __init__(self, volumes: dict):
        self.volumes = volumes

    def get_quote(self, mode, exchange_tokens):
        fetched = [
            {"symbolToken": token, "tradeVolume": self.volumes.get(token, 0)}
            for tokens in exchange_tokens.values() for token in tokens
        ]
        return {"fetched": fetched}


def test_filter_by_volume_keeps_only_liquid_names():
    rows = [{"name": "A", "token": "1"}, {"name": "B", "token": "2"}]
    rest = FakeQuoteRest({"1": 1_000_000, "2": 100})
    kept = filter_by_volume(rest, rows, min_volume=500_000)
    assert [r["name"] for r in kept] == ["A"]


def test_filter_by_volume_empty_rows_short_circuits():
    rest = FakeQuoteRest({})
    assert filter_by_volume(rest, [], min_volume=1) == []


def test_build_universe_end_to_end():
    instruments = [
        {"name": "RELIANCE", "instrumenttype": "OPTSTK", "exch_seg": "NFO"},
        {"name": "RELIANCE", "instrumenttype": "", "exch_seg": "NSE", "symbol": "RELIANCE-EQ", "token": "1"},
    ]
    rest = FakeQuoteRest({"1": 1_000_000})
    rows = build_universe(rest, instruments, min_volume=500_000)
    assert [r["name"] for r in rows] == ["RELIANCE"]
