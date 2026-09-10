from research.framework.backtest_engine import ClosedTrade
from research.framework.metrics import breakdown_by_direction, compute_metrics
from research.framework.report import render_report, write_report


def _trade(net_pnl, direction="up"):
    return ClosedTrade(
        underlying="TEST", strategy="trend_following", direction=direction, entry_date=None, exit_date=None,
        entry_price=100.0, exit_price=100.0 + net_pnl, quantity=1, gross_pnl=net_pnl, costs=0.0,
        net_pnl=net_pnl, exit_reason="target", r_multiple=1.0, entry_regime_trend="up", entry_regime_volatility="normal",
    )


def test_render_report_contains_expected_sections():
    trades = [_trade(100.0), _trade(-50.0, direction="down")]
    overall = compute_metrics(trades, 100000.0)
    breakdowns = {"direction": breakdown_by_direction(trades, 100000.0)}
    content = render_report(
        "Test Report", overall, breakdowns, walk_forward_summary="wf ok", monte_carlo_summary="mc ok",
    )
    assert "# Test Report" in content
    assert "## Overall" in content
    assert "## Breakdown by direction" in content
    assert "## Walk-Forward Validation" in content
    assert "wf ok" in content
    assert "## Monte Carlo Simulation" in content
    assert "## Parameter Sensitivity" not in content


def test_write_report_creates_file(tmp_path, monkeypatch):
    import research.framework.report as report_mod
    monkeypatch.setattr(report_mod, "REPORTS_DIR", tmp_path)
    path = report_mod.write_report("test.md", "hello world")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "hello world"
