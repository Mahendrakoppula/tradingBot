import pytest

from risk.equity_protection import classify_equity_tier


def test_no_drawdown_is_normal_tier():
    tier = classify_equity_tier(current_capital=50_000, starting_capital=50_000)
    assert tier.tier == "NORMAL"
    assert tier.size_multiplier == 1.0
    assert tier.allow_new_trades is True


def test_gain_is_still_normal_tier():
    tier = classify_equity_tier(current_capital=55_000, starting_capital=50_000)
    assert tier.tier == "NORMAL"


def test_five_percent_drawdown_is_reduced_tier():
    tier = classify_equity_tier(current_capital=47_500, starting_capital=50_000)
    assert tier.tier == "REDUCED"
    assert tier.size_multiplier == 0.5
    assert tier.allow_new_trades is True


def test_ten_percent_drawdown_is_defensive_tier():
    tier = classify_equity_tier(current_capital=45_000, starting_capital=50_000)
    assert tier.tier == "DEFENSIVE"
    assert tier.size_multiplier == 0.25


def test_fifteen_percent_drawdown_is_stop_tier():
    tier = classify_equity_tier(current_capital=42_500, starting_capital=50_000)
    assert tier.tier == "STOP"
    assert tier.size_multiplier == 0.0
    assert tier.allow_new_trades is False


def test_beyond_fifteen_percent_is_still_stop_not_a_crash():
    tier = classify_equity_tier(current_capital=10_000, starting_capital=50_000)
    assert tier.tier == "STOP"


def test_just_inside_reduced_boundary_is_normal():
    tier = classify_equity_tier(current_capital=47_600, starting_capital=50_000)
    assert tier.tier == "NORMAL"


def test_invalid_starting_capital_raises():
    with pytest.raises(ValueError):
        classify_equity_tier(current_capital=1000, starting_capital=0)
