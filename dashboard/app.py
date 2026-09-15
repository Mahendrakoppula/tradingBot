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
from dashboard.paper_trading_queries import load_all_paper_trading_overviews
from dashboard.queries import INSTRUMENTS, INTERVALS, current_market_state, data_health_report, regime_history

st.set_page_config(page_title="Codex Dashboard", layout="wide")
st.title("Codex — Autonomous ML-Driven Index Options Trading System")
st.caption(
    "Research/validation dashboard only. No live trading logic exists - "
    "see README.md and backtesting/BACKTESTS.md for the full, honest status."
)

tab_health, tab_state, tab_backtest, tab_paper = st.tabs(["Data Health", "Market State", "Backtest", "Paper Trading"])

with tab_health:
    st.subheader("Data Health")
    rows = data_health_report()
    st.dataframe(pd.DataFrame([r.__dict__ for r in rows]), width='stretch')
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
        st.dataframe(history.tail(500), width='stretch', height=300)

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
        st.dataframe(trades_df, width='stretch')

with tab_paper:
    st.subheader("Paper Trading (Phase 14)")
    st.caption(
        "Reads the SAME persisted state paper_trading/daily_loop.py writes - "
        "never a separate copy of that logic. Scheduled Mon-Fri 15:45 IST on "
        "the shared instance (deploy/codex-paper-trading.timer). To review the "
        "live instance's history locally, sync .state/paper_trading/ down "
        "first (same pattern as data/raw/ - see README.md)."
    )

    overviews = load_all_paper_trading_overviews()
    any_activity = any(o.last_processed_timestamp is not None for o in overviews)
    if not any_activity:
        st.info(
            "No paper trading runs recorded yet in this local .state/ directory. "
            "Either the scheduled job hasn't fired yet, or its state hasn't been synced here."
        )

    for overview in overviews:
        st.markdown(f"#### {overview.instrument}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Last run", overview.last_processed_timestamp or "never")
        c2.metric("Completed trades", overview.summary.n_trades)
        c3.metric("Win rate", f"{overview.summary.win_rate:.1%}" if overview.summary.win_rate is not None else "N/A")
        c4.metric("Total P&L (per unit)", f"{overview.summary.total_pnl:.1f}")

        if overview.open_trade is not None:
            t = overview.open_trade
            st.write(
                f"**Open position**: {t.strategy_name} {t.direction} | strike {t.strike:.0f} | "
                f"entry premium {t.entry_premium:.2f} | stop {t.stop_price:.2f} | target {t.target_price:.2f}"
            )
        else:
            st.write("**Open position**: none")

        if overview.by_strategy:
            st.caption("By strategy")
            by_strategy_df = pd.DataFrame([
                {"strategy": name, "n_trades": s.n_trades, "win_rate": s.win_rate, "total_pnl": s.total_pnl}
                for name, s in overview.by_strategy.items()
            ])
            st.dataframe(by_strategy_df, width='stretch')

        if overview.completed_trades:
            trades_df = pd.DataFrame([
                {
                    "strategy": t.strategy_name, "direction": t.direction, "entry_regime": t.entry_regime,
                    "entry_timestamp": t.entry_timestamp, "exit_timestamp": t.exit_timestamp,
                    "exit_reason": t.exit_reason, "pnl": t.pnl,
                }
                for t in overview.completed_trades
            ])
            st.dataframe(trades_df, width='stretch')
        st.divider()
