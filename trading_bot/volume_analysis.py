"""Volume-confirmation heuristics: a breakout/move on below-average volume
is much more likely to fail than one backed by real participation - a
standard, well-established piece of TA practice. The `min_relative_volume`
default IS a first-cut, unbacktested threshold, same caveat class as
liquidity.py's MIN_OPEN_INTEREST/MAX_SPREAD_PCT.
"""


def relative_volume(current_volume: float, avg_volume: float) -> float:
    if avg_volume <= 0:
        return 0.0
    return current_volume / avg_volume


def volume_confirms_move(candle: dict, avg_volume: float, min_relative_volume: float = 1.5) -> bool:
    return relative_volume(candle.get("volume", 0) or 0, avg_volume) >= min_relative_volume
