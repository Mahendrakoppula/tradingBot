from research.framework.market_structure import find_structure_events, label_swings
from trading_bot.support_resistance import SwingPoint


def test_label_swings_first_of_each_kind_is_none():
    points = [
        SwingPoint(index=1, price=100.0, kind="high"),
        SwingPoint(index=3, price=90.0, kind="low"),
    ]
    labeled = label_swings(points)
    assert labeled[0].label is None
    assert labeled[1].label is None


def test_label_swings_hh_hl_sequence():
    points = [
        SwingPoint(index=1, price=100.0, kind="high"),
        SwingPoint(index=3, price=90.0, kind="low"),
        SwingPoint(index=5, price=105.0, kind="high"),  # HH
        SwingPoint(index=7, price=95.0, kind="low"),  # HL
    ]
    labeled = label_swings(points)
    by_index = {ls.swing.index: ls.label for ls in labeled}
    assert by_index[5] == "HH"
    assert by_index[7] == "HL"


def test_label_swings_lh_ll_sequence():
    points = [
        SwingPoint(index=1, price=100.0, kind="high"),
        SwingPoint(index=3, price=90.0, kind="low"),
        SwingPoint(index=5, price=95.0, kind="high"),  # LH
        SwingPoint(index=7, price=80.0, kind="low"),  # LL
    ]
    labeled = label_swings(points)
    by_index = {ls.swing.index: ls.label for ls in labeled}
    assert by_index[5] == "LH"
    assert by_index[7] == "LL"


def test_find_structure_events_continuation_is_bos():
    # A clean sequence of HH/HL - every new extreme continues the uptrend.
    points = [
        SwingPoint(index=1, price=100.0, kind="high"),
        SwingPoint(index=3, price=90.0, kind="low"),
        SwingPoint(index=5, price=105.0, kind="high"),  # HH -> bos_up
        SwingPoint(index=7, price=95.0, kind="low"),  # HL -> no event (expected continuation shape)
        SwingPoint(index=9, price=110.0, kind="high"),  # HH -> bos_up
    ]
    events = find_structure_events(points)
    kinds = [(e.kind, e.index) for e in events]
    assert ("bos_up", 5) in kinds
    assert ("bos_up", 9) in kinds
    assert not any(k == "choch_down" for k, _ in kinds)


def test_find_structure_events_reversal_is_choch():
    # Uptrend established (HH then HL), then a lower low breaks it.
    points = [
        SwingPoint(index=1, price=100.0, kind="high"),
        SwingPoint(index=3, price=90.0, kind="low"),
        SwingPoint(index=5, price=105.0, kind="high"),  # HH -> bos_up, trend=up
        SwingPoint(index=7, price=95.0, kind="low"),  # HL, trend stays up
        SwingPoint(index=9, price=85.0, kind="low"),  # LL while trend is up -> choch_down
    ]
    events = find_structure_events(points)
    kinds = [(e.kind, e.index) for e in events]
    assert ("choch_down", 9) in kinds
