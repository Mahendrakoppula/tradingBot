import pytest

from trading_bot.volume_analysis import relative_volume, volume_confirms_move


def test_relative_volume_basic():
    assert relative_volume(150, 100) == pytest.approx(1.5)


def test_relative_volume_zero_avg_is_zero_not_crash():
    assert relative_volume(150, 0) == 0.0


def test_volume_confirms_move_above_threshold():
    assert volume_confirms_move({"volume": 200}, avg_volume=100, min_relative_volume=1.5) is True


def test_volume_confirms_move_below_threshold():
    assert volume_confirms_move({"volume": 120}, avg_volume=100, min_relative_volume=1.5) is False


def test_volume_confirms_move_missing_volume_key():
    assert volume_confirms_move({}, avg_volume=100) is False
