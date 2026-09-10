from features.black_scholes import Greeks
from portfolio.greek_aggregation import (
    PortfolioRiskLimits,
    Position,
    aggregate_portfolio_greeks,
    check_portfolio_risk,
)


def _greeks(delta=0.5, gamma=0.001, theta=-10.0, vega=5.0) -> Greeks:
    return Greeks(delta=delta, gamma=gamma, theta_per_day=theta, vega_per_1pct_vol=vega, rho_per_1pct_rate=0.0)


def test_aggregation_sums_across_positions():
    positions = [
        Position("NIFTY", "CE", quantity=100, greeks=_greeks(delta=0.5, gamma=0.001, theta=-10, vega=5)),
        Position("BANKNIFTY", "PE", quantity=50, greeks=_greeks(delta=-0.4, gamma=0.002, theta=-8, vega=6)),
    ]
    result = aggregate_portfolio_greeks(positions)
    assert result.total_delta == 100 * 0.5 + 50 * -0.4
    assert result.total_gamma == 100 * 0.001 + 50 * 0.002
    assert result.total_theta_per_day == 100 * -10 + 50 * -8
    assert result.total_vega_per_1pct_vol == 100 * 5 + 50 * 6


def test_short_positions_use_negative_quantity_correctly():
    positions = [Position("NIFTY", "CE", quantity=-100, greeks=_greeks(delta=0.5))]
    result = aggregate_portfolio_greeks(positions)
    assert result.total_delta == -50.0


def test_delta_by_instrument_accumulates_multiple_positions_same_instrument():
    positions = [
        Position("NIFTY", "CE", quantity=100, greeks=_greeks(delta=0.5)),
        Position("NIFTY", "PE", quantity=100, greeks=_greeks(delta=-0.3)),
    ]
    result = aggregate_portfolio_greeks(positions)
    assert result.delta_by_instrument["NIFTY"] == 100 * 0.5 + 100 * -0.3


def test_empty_portfolio_has_zero_exposure():
    result = aggregate_portfolio_greeks([])
    assert result.total_delta == 0.0
    assert result.delta_by_instrument == {}


def _limits(**overrides) -> PortfolioRiskLimits:
    defaults = dict(max_abs_delta=1000, max_abs_gamma=10, max_abs_vega=1000, max_abs_theta=5000)
    defaults.update(overrides)
    return PortfolioRiskLimits(**defaults)


def test_within_all_limits_passes():
    positions = [Position("NIFTY", "CE", quantity=100, greeks=_greeks(delta=0.5))]
    portfolio = aggregate_portfolio_greeks(positions)
    result = check_portfolio_risk(portfolio, _limits())
    assert result.within_limits is True
    assert result.breaches == []


def test_delta_breach_is_reported():
    positions = [Position("NIFTY", "CE", quantity=10_000, greeks=_greeks(delta=0.5))]
    portfolio = aggregate_portfolio_greeks(positions)
    result = check_portfolio_risk(portfolio, _limits(max_abs_delta=1000))
    assert result.within_limits is False
    assert any("delta" in b.lower() for b in result.breaches)


def test_negative_delta_breach_is_detected_via_absolute_value():
    positions = [Position("NIFTY", "PE", quantity=10_000, greeks=_greeks(delta=-0.5))]
    portfolio = aggregate_portfolio_greeks(positions)
    result = check_portfolio_risk(portfolio, _limits(max_abs_delta=1000))
    assert result.within_limits is False


def test_concentration_limit_flags_single_instrument_dominance():
    positions = [
        Position("NIFTY", "CE", quantity=1000, greeks=_greeks(delta=0.5)),
        Position("SENSEX", "CE", quantity=10, greeks=_greeks(delta=0.5)),
    ]
    portfolio = aggregate_portfolio_greeks(positions)
    result = check_portfolio_risk(portfolio, _limits(max_abs_delta=10_000, max_single_instrument_delta_share=0.7))
    assert result.within_limits is False
    assert any("concentration" in b.lower() for b in result.breaches)


def test_balanced_exposure_across_instruments_does_not_trigger_concentration_limit():
    positions = [
        Position("NIFTY", "CE", quantity=100, greeks=_greeks(delta=0.5)),
        Position("BANKNIFTY", "CE", quantity=100, greeks=_greeks(delta=0.5)),
        Position("SENSEX", "CE", quantity=100, greeks=_greeks(delta=0.5)),
    ]
    portfolio = aggregate_portfolio_greeks(positions)
    result = check_portfolio_risk(
        portfolio, _limits(max_abs_delta=10_000, max_abs_vega=10_000, max_single_instrument_delta_share=0.5)
    )
    assert result.within_limits is True


def test_default_concentration_share_of_one_never_triggers():
    positions = [Position("NIFTY", "CE", quantity=100, greeks=_greeks(delta=0.5))]
    portfolio = aggregate_portfolio_greeks(positions)
    result = check_portfolio_risk(portfolio, _limits(max_abs_delta=10_000))
    assert result.within_limits is True
