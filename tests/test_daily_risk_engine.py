from risk.daily_risk_engine import DailyRiskEngine


def _engine() -> DailyRiskEngine:
    return DailyRiskEngine(max_daily_loss=2000.0, profit_protection_level=800.0, profit_selectivity_level=1000.0)


def test_zero_pnl_allows_normal_trading():
    engine = _engine()
    decision = engine.evaluate()
    assert decision.allow_new_trades is True
    assert decision.min_confidence_required == 0.50


def test_loss_within_limit_still_allows_trading():
    engine = _engine()
    engine.record_realized_pnl(-1500)
    decision = engine.evaluate()
    assert decision.allow_new_trades is True


def test_hard_loss_limit_blocks_new_trades():
    engine = _engine()
    engine.record_realized_pnl(-2000)
    decision = engine.evaluate()
    assert decision.allow_new_trades is False
    assert "Hard daily loss limit" in decision.reason


def test_loss_beyond_the_limit_still_blocks_not_crashes():
    engine = _engine()
    engine.record_realized_pnl(-1200)
    engine.record_realized_pnl(-5000)
    decision = engine.evaluate()
    assert decision.allow_new_trades is False


def test_profit_at_protection_level_raises_confidence_bar():
    engine = _engine()
    engine.record_realized_pnl(800)
    decision = engine.evaluate()
    assert decision.allow_new_trades is True
    assert decision.min_confidence_required == 0.70


def test_profit_at_selectivity_level_raises_bar_further():
    engine = _engine()
    engine.record_realized_pnl(1000)
    decision = engine.evaluate()
    assert decision.allow_new_trades is True
    assert decision.min_confidence_required == 0.85


def test_profit_thresholds_are_never_a_forced_stop():
    """Explicit spec requirement: Rs.800-1,000 daily profit is a
    selectivity threshold, never a stop point or a forced daily target -
    trading must still be ALLOWED well past it, just more selectively."""
    engine = _engine()
    engine.record_realized_pnl(50_000)  # an enormous profit day
    decision = engine.evaluate()
    assert decision.allow_new_trades is True


def test_reset_day_clears_accumulated_pnl():
    engine = _engine()
    engine.record_realized_pnl(-2000)
    assert engine.evaluate().allow_new_trades is False
    engine.reset_day()
    assert engine.daily_pnl == 0.0
    assert engine.evaluate().allow_new_trades is True


def test_record_realized_pnl_accumulates_across_multiple_trades():
    engine = _engine()
    engine.record_realized_pnl(-500)
    engine.record_realized_pnl(-700)
    engine.record_realized_pnl(-900)
    assert engine.daily_pnl == -2100
    assert engine.evaluate().allow_new_trades is False
