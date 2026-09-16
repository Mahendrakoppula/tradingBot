"""Renders one run's full output (overall + breakdown metrics tables,
walk-forward/Monte Carlo/sensitivity summaries) to a Markdown file under
research/reports/ - offline only, no live dashboard, consistent with this
framework's "separate, offline" scope (see .claude/plans/async-jumping-karp.md).
"""
import datetime as dt
from pathlib import Path

from research.framework.metrics import Metrics

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

_TABLE_HEADER = "| Group | Trades | Win Rate | Profit Factor | Expectancy | Avg R | Sharpe-like | Max DD % | Net P&L |"
_TABLE_SEP = "|---|---|---|---|---|---|---|---|---|"


def _fmt(x, nd=2):
    if x is None:
        return "-"
    if isinstance(x, float):
        if x == float("inf"):
            return "inf"
        if x == float("-inf"):
            return "-inf"
        return f"{x:.{nd}f}"
    return str(x)


def _metrics_row(label: str, m: Metrics) -> str:
    return (
        f"| {label} | {m.trade_count} | {_fmt(m.win_rate)} | {_fmt(m.profit_factor)} | {_fmt(m.expectancy)} | "
        f"{_fmt(m.avg_r_multiple)} | {_fmt(m.sharpe_like, 3)} | {_fmt(m.max_drawdown_pct)} | {_fmt(m.total_net_pnl)} |"
    )


def render_report(
    title: str,
    overall: Metrics,
    breakdowns: dict = None,
    walk_forward_summary: str = None,
    monte_carlo_summary: str = None,
    sensitivity_summary: str = None,
) -> str:
    lines = [f"# {title}", "", f"_Generated {dt.datetime.now().isoformat(timespec='seconds')}_", ""]
    lines += ["## Overall", "", _TABLE_HEADER, _TABLE_SEP, _metrics_row("overall", overall), ""]

    if breakdowns:
        for name, groups in breakdowns.items():
            lines += [f"## Breakdown by {name}", "", _TABLE_HEADER, _TABLE_SEP]
            for key, m in groups.items():
                lines.append(_metrics_row(str(key), m))
            lines.append("")

    if walk_forward_summary:
        lines += ["## Walk-Forward Validation", "", walk_forward_summary, ""]
    if monte_carlo_summary:
        lines += ["## Monte Carlo Simulation", "", monte_carlo_summary, ""]
    if sensitivity_summary:
        lines += ["## Parameter Sensitivity", "", sensitivity_summary, ""]

    return "\n".join(lines)


def write_report(filename: str, content: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / filename
    path.write_text(content, encoding="utf-8")
    return path
