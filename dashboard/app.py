"""Codex dashboard (spec Phase 15) - Streamlit. Read-only over
everything built so far: data health, current market state, and an
on-demand backtest viewer. No live trading logic exists anywhere in
this repo - this page displays research/validation artifacts only, and
says so, rather than implying anything here is production-ready.

Run: streamlit run dashboard/app.py

`streamlit run <script>` puts the SCRIPT's own directory on sys.path,
not the project root (unlike `python -m pytest`/`python -m data.pull_history`
run from the root) - `dashboard` itself has to be importable as a
package from ITS PARENT for `from dashboard.queries import ...` below
to work at all, hence the path fix. (A same-named `dashboard/data.py`
module used to sit next to this file and collided with the top-level
data/ package once Streamlit put this directory on sys.path - confirmed
live as a real "'data' is not a package" ImportError, not just a
hypothetical; renamed to dashboard/queries.py to fix it properly rather
than paper over the collision with more sys.path ordering tricks.)
"""
import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd
import streamlit as st

from dashboard.backtest_data import run_backtest_for_dashboard
from dashboard.queries import INSTRUMENTS, INTERVALS, current_market_state, data_health_report, regime_history

st.set_page_config(page_title="Codex Dashboard", layout="wide")
st.title("Codex — Autonomous ML-Driven Index Options Trading System")
st.caption(
    "Research/validation dashboard only. No live trading logic exists - "
    "see README.md and backtesting/BACKTESTS.md for the full, honest status."
)

tab_health, tab_state, tab_backtest = st.tabs(["Data Health", "Market State", "Backtest"])

with tab_health:
    st.subheader("Data Health")
    rows = data_health_report()
    st.dataframe(pd.DataFrame([r.__dict__ for r in rows]), use_container_width=True)
    st.caption("quality_errors/quality_warnings from data/quality.py's Data Quality Engine.")

with tab_state:
    st.subheader("Current Market State")
    col_a, col_b = st.columns(2)
    instrument = col_a.selectbox("Instrument", INSTRUMENTS, key="state_instrument")
    interval = col_b.selectbox("Interval", INTERVALS, index=INTERVALS.index("ONE_DAY"), key="state_interval")

    state = current_market_state(instrument, interval)
    if state is None:
        st.warning(f"No data pulled yet for {instrument} {interval} - run `python -m data.pull_history` first.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Regime", state.regime)
        c2.metric("Structure", state.structure.trend)
        c3.metric("Volatility", state.volatility.label)
        c4.metric("Momentum", state.momentum.label)

        history = regime_history(instrument, interval)
        st.caption("Regime over time (last 500 bars)")
        st.dataframe(history.tail(500), use_container_width=True, height=300)

with tab_backtest:
    st.subheader("Backtest (informational only)")
    st.caption(
        "This runs backtesting/event_loop.py live for interactive viewing. "
        "See backtesting/BACKTESTS.md for the full validation history "
        "(walk-forward, bootstrap/Monte Carlo, attribution) - nothing shown "
        "here alone should be read as a validated result."
    )
    col_a, col_b = st.columns(2)
    bt_instrument = col_a.selectbox("Instrument", INSTRUMENTS, key="bt_instrument")
    use_selector = col_b.checkbox("Use contract selector (Phase 9, opt-in)", value=False)

    bt_result = run_backtest_for_dashboard(bt_instrument, use_contract_selector=use_selector)
    if bt_result is None:
        st.warning(f"No data pulled yet for {bt_instrument} - run `python -m data.pull_history` first.")
    else:
        summary = bt_result.summary
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Trades", summary.n_trades)
        c2.metric("Win rate", f"{summary.win_rate:.1%}" if summary.win_rate is not None else "N/A")
        c3.metric("Total P&L (per unit)", f"{summary.total_pnl:.1f}")
        c4.metric("Max drawdown", f"{summary.max_drawdown:.1f}")

        trades_df = pd.DataFrame([
            {
                "strategy": t.strategy_name, "direction": t.direction, "entry_regime": t.entry_regime,
                "entry_timestamp": t.entry_timestamp, "exit_timestamp": t.exit_timestamp,
                "exit_reason": t.exit_reason, "pnl": t.pnl,
            }
            for t in bt_result.trades
        ])
        st.dataframe(trades_df, use_container_width=True)
