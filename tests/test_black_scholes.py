import math

import pytest

from features.black_scholes import bs_greeks, bs_price

# Verified via mathematically-guaranteed Black-Scholes properties (put-call
# parity, bounds, monotonicity) rather than hardcoded "known" reference
# prices from memory - more robust and independently checkable.

SPOT, STRIKE, T, R, VOL = 24500.0, 24500.0, 30 / 365, 0.07, 0.15


def test_put_call_parity_holds():
    call = bs_price(SPOT, STRIKE, T, R, VOL, "CE")
    put = bs_price(SPOT, STRIKE, T, R, VOL, "PE")
    lhs = call - put
    rhs = SPOT - STRIKE * math.exp(-R * T)
    assert lhs == pytest.approx(rhs, abs=1e-6)


def test_call_price_within_no_arbitrage_bounds():
    call = bs_price(SPOT, STRIKE, T, R, VOL, "CE")
    lower = max(0.0, SPOT - STRIKE * math.exp(-R * T))
    upper = SPOT
    assert lower - 1e-9 <= call <= upper + 1e-9


def test_deep_itm_call_price_approaches_intrinsic_plus_a_bit():
    deep_itm_call = bs_price(spot=30000, strike=20000, time_to_expiry_years=T, risk_free_rate=R, volatility=VOL, option_type="CE")
    intrinsic = 30000 - 20000 * math.exp(-R * T)
    # deep ITM: extrinsic value should be small relative to spot
    assert deep_itm_call == pytest.approx(intrinsic, rel=0.01)


def test_deep_otm_call_price_is_near_zero():
    deep_otm_call = bs_price(spot=20000, strike=30000, time_to_expiry_years=T, risk_free_rate=R, volatility=VOL, option_type="CE")
    assert deep_otm_call < 1.0


def test_at_expiry_price_is_pure_intrinsic_value():
    itm_call = bs_price(SPOT, strike=24000, time_to_expiry_years=0, risk_free_rate=R, volatility=VOL, option_type="CE")
    assert itm_call == pytest.approx(500.0)
    otm_call = bs_price(SPOT, strike=25000, time_to_expiry_years=0, risk_free_rate=R, volatility=VOL, option_type="CE")
    assert otm_call == 0.0


def test_invalid_option_type_raises():
    with pytest.raises(ValueError):
        bs_price(SPOT, STRIKE, T, R, VOL, "XX")


def test_call_price_increases_with_volatility():
    low_vol_price = bs_price(SPOT, STRIKE, T, R, 0.10, "CE")
    high_vol_price = bs_price(SPOT, STRIKE, T, R, 0.30, "CE")
    assert high_vol_price > low_vol_price


def test_call_and_put_gamma_and_vega_are_identical_at_same_strike():
    call_greeks = bs_greeks(SPOT, STRIKE, T, R, VOL, "CE")
    put_greeks = bs_greeks(SPOT, STRIKE, T, R, VOL, "PE")
    assert call_greeks.gamma == pytest.approx(put_greeks.gamma, rel=1e-9)
    assert call_greeks.vega_per_1pct_vol == pytest.approx(put_greeks.vega_per_1pct_vol, rel=1e-9)


def test_call_delta_between_zero_and_one_put_delta_between_minus_one_and_zero():
    call_greeks = bs_greeks(SPOT, STRIKE, T, R, VOL, "CE")
    put_greeks = bs_greeks(SPOT, STRIKE, T, R, VOL, "PE")
    assert 0.0 < call_greeks.delta < 1.0
    assert -1.0 < put_greeks.delta < 0.0


def test_deep_itm_call_delta_approaches_one():
    greeks = bs_greeks(spot=30000, strike=20000, time_to_expiry_years=T, risk_free_rate=R, volatility=VOL, option_type="CE")
    assert greeks.delta > 0.99


def test_deep_otm_call_delta_approaches_zero():
    greeks = bs_greeks(spot=20000, strike=30000, time_to_expiry_years=T, risk_free_rate=R, volatility=VOL, option_type="CE")
    assert greeks.delta < 0.01


def test_at_expiry_greeks_are_a_step_function_not_an_error():
    greeks = bs_greeks(SPOT, strike=24000, time_to_expiry_years=0, risk_free_rate=R, volatility=VOL, option_type="CE")
    assert greeks.delta == 1.0
    assert greeks.gamma == 0.0
    assert greeks.theta_per_day == 0.0


def test_gamma_and_vega_are_positive():
    greeks = bs_greeks(SPOT, STRIKE, T, R, VOL, "CE")
    assert greeks.gamma > 0
    assert greeks.vega_per_1pct_vol > 0
