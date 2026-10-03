"""Indikator bersama antar-strategi: ATR Wilder + buffer SL dinamis (pure pandas)."""
from __future__ import annotations

import pandas as pd


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """True Range + Wilder smoothing via ewm(alpha=1/period)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def atr_value(df: pd.DataFrame, period: int = 14) -> float:
    """ATR bar closed [-2]. NaN bila data kurang."""
    try:
        return float(atr_series(df, period).iloc[-2])
    except Exception:
        return float("nan")


def sl_buffer(atr_v: float, mult: float = 0.5, floor: float = 1.00) -> float:
    """Buffer SL = max(floor, mult*ATR). Fail-safe ke floor bila ATR NaN.

    M15 gold: spread $0.25-0.50 + noise $2-5 -> buffer fix $1.00 terlalu rapat.
    0.5*ATR(14) menempatkan SL di luar noise rata-rata tanpa terlalu lebar.
    """
    import math
    try:
        if math.isnan(float(atr_v)):
            return float(floor)
        return round(max(float(floor), float(mult) * float(atr_v)), 2)
    except Exception:
        return float(floor)
