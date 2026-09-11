"""Trade Ranking Engine (spec Phase 9). Currently a thin, honest
first pass: ranks candidate signals by confidence alone, since
confidence is the only per-signal quality score that exists without a
validated ML model - Model 1 (models/EXPERIMENTS.md) didn't beat its
own baseline, so nothing here pretends a probability-of-success ranking
exists yet. This module exists as the single place that decision will
be made once a real trade-quality model (spec's Model 7) is validated,
rather than leaving `max(signals, key=confidence)` scattered inline
(backtesting/event_loop.py used to do exactly that before this existed).
"""
from strategies.base import Signal


def rank_signals(signals: list[Signal]) -> list[Signal]:
    """Highest confidence first. Ties keep their original relative
    order (Python's sort is stable) rather than being broken
    arbitrarily."""
    return sorted(signals, key=lambda s: s.confidence, reverse=True)


def select_best_signal(signals: list[Signal]) -> Signal | None:
    ranked = rank_signals(signals)
    return ranked[0] if ranked else None
