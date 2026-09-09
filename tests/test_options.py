import pytest

from trading_bot.options import find_spot_instrument


def _index_row(name, exch_seg, token):
    return {"token": token, "symbol": name, "name": name, "instrumenttype": "AMXIDX", "exch_seg": exch_seg}


def _equity_row(name, token):
    return {"token": token, "symbol": f"{name}-EQ", "name": name, "instrumenttype": "", "exch_seg": "NSE"}


def test_find_spot_instrument_nse_index():
    instruments = [_index_row("NIFTY", "NSE", "99926000"), _index_row("SENSEX", "BSE", "99919000")]
    row = find_spot_instrument(instruments, "NIFTY")
    assert row["token"] == "99926000"
    assert row["exch_seg"] == "NSE"


def test_find_spot_instrument_bse_index_sensex():
    # SENSEX is on BSE, not NSE - live-verified 2026-09-09 (token 99919000)
    instruments = [_index_row("NIFTY", "NSE", "99926000"), _index_row("SENSEX", "BSE", "99919000")]
    row = find_spot_instrument(instruments, "SENSEX")
    assert row["token"] == "99919000"
    assert row["exch_seg"] == "BSE"


def test_find_spot_instrument_case_insensitive():
    instruments = [_index_row("SENSEX", "BSE", "99919000")]
    assert find_spot_instrument(instruments, "sensex")["token"] == "99919000"


def test_find_spot_instrument_nse_equity():
    instruments = [_equity_row("RELIANCE", "2885")]
    row = find_spot_instrument(instruments, "RELIANCE")
    assert row["token"] == "2885"
    assert row["symbol"] == "RELIANCE-EQ"


def test_find_spot_instrument_index_preferred_over_equity_same_name():
    # if a name somehow matches both an index and an equity row, index wins
    instruments = [_index_row("NIFTY", "NSE", "99926000")]
    assert find_spot_instrument(instruments, "NIFTY")["instrumenttype"] == "AMXIDX"


def test_find_spot_instrument_not_found_raises():
    with pytest.raises(LookupError):
        find_spot_instrument([], "GHOST")
