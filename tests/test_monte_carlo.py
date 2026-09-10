import random

import pytest

from backtesting.monte_carlo import bootstrap_total_pnl, monte_carlo_trade_sequence
from backtesting.trade_record import Trade


def _trade(pnl: float) -> Trade:
    return Trade("t", "CE", 0, None, 100.0, 5.0, 100.0, None, stop_price=95, target_price=110, pnl=pnl)


def test_bootstrap_raises_on_no_closed_trades():
    with pytest.raises(ValueError):
        bootstrap_total_pnl([])


def test_bootstrap_observed_total_is_the_exact_sample_sum():
    trades = [_trade(10), _trade(-5), _trade(20)]
    result = bootstrap_total_pnl(trades, n_resamples=100, rng=random.Random(0))
    assert result.observed_total_pnl == 25.0


def test_bootstrap_is_deterministic_given_a_seeded_rng():
    trades = [_trade(10), _trade(-5), _trade(20), _trade(-8)]
    r1 = bootstrap_total_pnl(trades, n_resamples=500, rng=random.Random(42))
    r2 = bootstrap_total_pnl(trades, n_resamples=500, rng=random.Random(42))
    assert r1.ci_low == r2.ci_low
    assert r1.ci_high == r2.ci_high
    assert r1.mean_of_resampled_total_pnl == r2.mean_of_resampled_total_pnl


def test_bootstrap_ci_low_never_exceeds_ci_high():
    trades = [_trade(10), _trade(-5), _trade(20), _trade(-30), _trade(15)]
    result = bootstrap_total_pnl(trades, n_resamples=2000, rng=random.Random(1))
    assert result.ci_low <= result.ci_high


def test_bootstrap_of_all_positive_trades_can_never_show_a_negative_ci_low():
    """Resampling WITH REPLACEMENT from an all-positive sample can only
    ever produce positive sums - a real mathematical guarantee, not an
    empirical tendency, so this must hold exactly regardless of rng."""
    trades = [_trade(5), _trade(10), _trade(3), _trade(8)]
    result = bootstrap_total_pnl(trades, n_resamples=2000, rng=random.Random(7))
    assert result.ci_low > 0
    assert result.fraction_of_resamples_profitable == 1.0


def test_bootstrap_mean_is_a_reasonable_estimate_of_the_observed_total():
    trades = [_trade(p) for p in [12, -4, 7, -9, 15, 3, -6, 10, -2, 8]]
    result = bootstrap_total_pnl(trades, n_resamples=20_000, rng=random.Random(3))
    assert result.mean_of_resampled_total_pnl == pytest.approx(result.observed_total_pnl, abs=5.0)


# --- Monte Carlo trade-sequence ---

def test_monte_carlo_raises_on_no_closed_trades():
    with pytest.raises(ValueError):
        monte_carlo_trade_sequence([])


def test_monte_carlo_observed_drawdown_matches_original_order():
    from backtesting.metrics import max_drawdown
    trades = [_trade(100), _trade(-40), _trade(30), _trade(-50)]
    result = monte_carlo_trade_sequence(trades, n_simulations=100, rng=random.Random(0))
    assert result.observed_max_drawdown == max_drawdown([100, -40, 30, -50])


def test_monte_carlo_is_deterministic_given_a_seeded_rng():
    trades = [_trade(p) for p in [50, -20, 30, -40, 10]]
    r1 = monte_carlo_trade_sequence(trades, n_simulations=500, rng=random.Random(99))
    r2 = monte_carlo_trade_sequence(trades, n_simulations=500, rng=random.Random(99))
    assert r1.worst_case_max_drawdown == r2.worst_case_max_drawdown
    assert r1.mean_simulated_max_drawdown == r2.mean_simulated_max_drawdown


def test_monte_carlo_all_winning_trades_always_has_zero_drawdown_in_any_order():
    trades = [_trade(5), _trade(10), _trade(3)]
    result = monte_carlo_trade_sequence(trades, n_simulations=200, rng=random.Random(4))
    assert result.observed_max_drawdown == 0.0
    assert result.mean_simulated_max_drawdown == 0.0
    assert result.worst_case_max_drawdown == 0.0


def test_monte_carlo_fraction_worse_than_observed_is_a_valid_fraction():
    trades = [_trade(p) for p in [40, -30, 20, -25, 15, -10]]
    result = monte_carlo_trade_sequence(trades, n_simulations=1000, rng=random.Random(5))
    assert 0.0 <= result.fraction_worse_than_observed <= 1.0
