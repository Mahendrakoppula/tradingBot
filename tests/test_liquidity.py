from trading_bot.liquidity import check_liquidity, entry_limit_price, exit_limit_price


def _quote(bid=100.0, bid_qty=500, ask=102.0, ask_qty=500, oi=1000, ltp=101.0):
    return {
        "ltp": ltp,
        "opnInterest": oi,
        "depth": {
            "buy": [{"price": bid, "quantity": bid_qty}],
            "sell": [{"price": ask, "quantity": ask_qty}],
        },
    }


def test_liquid_contract_passes():
    ok, reason = check_liquidity(_quote(), max_spread_pct=8.0, min_oi=100)
    assert ok is True


def test_no_ask_depth_fails():
    quote = _quote(ask=0.0, ask_qty=0)
    ok, reason = check_liquidity(quote, max_spread_pct=8.0, min_oi=100)
    assert ok is False
    assert "no ask-side depth" in reason


def test_low_open_interest_fails():
    quote = _quote(oi=10)
    ok, reason = check_liquidity(quote, max_spread_pct=8.0, min_oi=100)
    assert ok is False
    assert "open interest" in reason


def test_wide_spread_fails():
    quote = _quote(bid=90.0, ask=110.0)  # ~18% spread
    ok, reason = check_liquidity(quote, max_spread_pct=8.0, min_oi=100)
    assert ok is False
    assert "spread" in reason


def test_zero_bid_does_not_crash_and_still_checks_ask_and_oi():
    quote = _quote(bid=0.0, bid_qty=0)
    ok, reason = check_liquidity(quote, max_spread_pct=8.0, min_oi=100)
    assert ok is True  # no bid to compare against - spread check skipped, ask+OI still fine


def test_entry_limit_price_is_ask_plus_buffer():
    quote = _quote(ask=100.0)
    price = entry_limit_price(quote, buffer_pct=0.5)
    assert price == 100.5


def test_entry_limit_price_falls_back_to_ltp_when_no_depth():
    quote = {"ltp": 50.0, "depth": {"sell": []}}
    price = entry_limit_price(quote, buffer_pct=1.0)
    assert price == 50.5


def test_exit_limit_price_is_bid_minus_buffer():
    quote = _quote(bid=100.0)
    price = exit_limit_price(quote, buffer_pct=0.5)
    assert price == 99.5


def test_exit_limit_price_falls_back_to_ltp_when_no_depth():
    quote = {"ltp": 50.0, "depth": {"buy": []}}
    price = exit_limit_price(quote, buffer_pct=1.0)
    assert price == 49.5
