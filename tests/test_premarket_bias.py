from unittest.mock import patch

from trading_bot.premarket_bias import allows_direction, compute_premarket_bias, format_bias_line


class FakeRest:
    def __init__(self, vix):
        self.vix = vix

    def get_ltp(self, exchange, tradingsymbol, symboltoken):
        return {"ltp": str(self.vix)}


def _compute(vix, us_change, calendar):
    rest = FakeRest(vix)
    with patch("trading_bot.premarket_bias.get_global_change_pct", return_value=us_change), \
         patch("trading_bot.premarket_bias.get_economic_calendar", return_value=calendar):
        return compute_premarket_bias(rest, us_move_threshold_pct=0.5, vix_caution_level=20.0)


def test_bullish_on_strong_overnight_up_move():
    bias = _compute(vix=15.0, us_change=1.2, calendar=None)
    assert bias["bias"] == "BULLISH"


def test_bearish_on_strong_overnight_down_move():
    bias = _compute(vix=15.0, us_change=-1.2, calendar=None)
    assert bias["bias"] == "BEARISH"


def test_neutral_on_weak_overnight_move():
    bias = _compute(vix=15.0, us_change=0.1, calendar=None)
    assert bias["bias"] == "NEUTRAL"


def test_cautious_when_vix_elevated_even_with_bullish_overnight_move():
    bias = _compute(vix=25.0, us_change=1.2, calendar=None)
    assert bias["bias"] == "CAUTIOUS"


def test_cautious_when_economic_events_scheduled():
    bias = _compute(vix=15.0, us_change=0.1, calendar=[{"event": "RBI policy"}])
    assert bias["bias"] == "CAUTIOUS"


def test_allows_direction_cautious_blocks_everything():
    bias = {"bias": "CAUTIOUS"}
    assert allows_direction(bias, "CE") is False
    assert allows_direction(bias, "PE") is False


def test_allows_direction_bullish_only_allows_calls():
    bias = {"bias": "BULLISH"}
    assert allows_direction(bias, "CE") is True
    assert allows_direction(bias, "PE") is False


def test_allows_direction_bearish_only_allows_puts():
    bias = {"bias": "BEARISH"}
    assert allows_direction(bias, "CE") is False
    assert allows_direction(bias, "PE") is True


def test_allows_direction_neutral_allows_both():
    bias = {"bias": "NEUTRAL"}
    assert allows_direction(bias, "CE") is True
    assert allows_direction(bias, "PE") is True


def test_format_bias_line_includes_reasons():
    bias = {"bias": "BULLISH", "reasons": ["S&P 500 closed up 1.20% overnight"]}
    line = format_bias_line(bias)
    assert "BULLISH" in line
    assert "S&P 500" in line
