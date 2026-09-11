from strategies.base import Signal
from strategies.ranking import rank_signals, select_best_signal


def _signal(name: str, confidence: float) -> Signal:
    return Signal(name, "CE", confidence, "test")


def test_rank_signals_sorts_by_confidence_descending():
    signals = [_signal("a", 0.3), _signal("b", 0.9), _signal("c", 0.5)]
    ranked = rank_signals(signals)
    assert [s.strategy_name for s in ranked] == ["b", "c", "a"]


def test_rank_signals_is_stable_for_ties():
    signals = [_signal("a", 0.5), _signal("b", 0.5), _signal("c", 0.5)]
    ranked = rank_signals(signals)
    assert [s.strategy_name for s in ranked] == ["a", "b", "c"]


def test_rank_signals_empty_list():
    assert rank_signals([]) == []


def test_select_best_signal_returns_highest_confidence():
    signals = [_signal("a", 0.3), _signal("b", 0.9)]
    assert select_best_signal(signals).strategy_name == "b"


def test_select_best_signal_returns_none_for_empty_list():
    assert select_best_signal([]) is None
