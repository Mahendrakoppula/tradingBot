"""Offline research framework for multi-strategy technical analysis
(regime detection, market structure, multi-timeframe context, scoring,
strategies, risk, backtesting, validation).

This package is research-only: it imports from `trading_bot/` (indicators,
chart_patterns, support_resistance, volume_analysis, costs) but is never
imported by `trading_bot/` itself, and nothing here changes the live bot's
behavior. See .claude/plans/async-jumping-karp.md for the staged build
plan and .claude/plans/goofy-plotting-sedgewick.md for the live bot's own
history. Only pieces validated here with genuine out-of-sample evidence
(walk-forward + Monte Carlo + sensitivity, see framework.walk_forward/
monte_carlo/sensitivity once built) are candidates for live promotion -
that promotion is a separate, explicit future decision, not automatic.
"""
