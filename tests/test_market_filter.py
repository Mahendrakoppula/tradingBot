from trading_bot.market_filter import TradeFilter


class FakeRest:
    def __init__(self, vix, pcr_rows, oi_rows):
        self.vix = vix
        self.pcr_rows = pcr_rows
        self.oi_rows = oi_rows

    def get_ltp(self, exchange, tradingsymbol, symboltoken):
        return {"ltp": str(self.vix)}

    def get_pcr(self):
        return self.pcr_rows

    def get_oi_buildup(self, expirytype, datatype):
        return self.oi_rows.get(datatype, [])


FILTER = TradeFilter(vix_min=11, vix_max=25, pcr_min=0.7, pcr_max=1.3)


def test_passes_under_good_conditions():
    rest = FakeRest(vix=15.0, pcr_rows=[{"tradingSymbol": "NIFTY28DEC26FUT", "pcr": 1.0}], oi_rows={})
    ok, reason = FILTER.should_trade(rest, "NIFTY")
    assert ok is True


def test_fails_when_vix_too_low():
    rest = FakeRest(vix=8.0, pcr_rows=[{"tradingSymbol": "NIFTY28DEC26FUT", "pcr": 1.0}], oi_rows={})
    ok, reason = FILTER.should_trade(rest, "NIFTY")
    assert ok is False
    assert "VIX" in reason


def test_fails_when_vix_too_high():
    rest = FakeRest(vix=30.0, pcr_rows=[{"tradingSymbol": "NIFTY28DEC26FUT", "pcr": 1.0}], oi_rows={})
    ok, reason = FILTER.should_trade(rest, "NIFTY")
    assert ok is False
    assert "VIX" in reason


def test_fails_when_pcr_out_of_band():
    rest = FakeRest(vix=15.0, pcr_rows=[{"tradingSymbol": "NIFTY28DEC26FUT", "pcr": 1.8}], oi_rows={})
    ok, reason = FILTER.should_trade(rest, "NIFTY")
    assert ok is False
    assert "PCR" in reason


def test_fails_on_fresh_long_built_up():
    rest = FakeRest(
        vix=15.0,
        pcr_rows=[{"tradingSymbol": "NIFTY28DEC26FUT", "pcr": 1.0}],
        oi_rows={"Long Built Up": [{"tradingSymbol": "NIFTY28DEC26FUT"}]},
    )
    ok, reason = FILTER.should_trade(rest, "NIFTY")
    assert ok is False
    assert "Long Built Up" in reason


def test_other_underlyings_buildup_does_not_affect_this_one():
    rest = FakeRest(
        vix=15.0,
        pcr_rows=[{"tradingSymbol": "NIFTY28DEC26FUT", "pcr": 1.0}],
        oi_rows={"Long Built Up": [{"tradingSymbol": "BANKNIFTY28DEC26FUT"}]},
    )
    ok, reason = FILTER.should_trade(rest, "NIFTY")
    assert ok is True


def test_missing_pcr_row_skips_pcr_check_gracefully():
    rest = FakeRest(vix=15.0, pcr_rows=[{"tradingSymbol": "RELIANCE28DEC26FUT", "pcr": 1.0}], oi_rows={})
    ok, reason = FILTER.should_trade(rest, "NIFTY")
    assert ok is True
