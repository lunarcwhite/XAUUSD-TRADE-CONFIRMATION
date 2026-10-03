"""Strategi D: Breakout Continuation (cabang ke-4, pelengkap pullback).

Aktif saat TRENDING tetapi harga RUNAWAY tanpa koreksi (pullback tak kunjung
datang): close menjauh dari zona EMA21-50 + momentum kuat searah tren.
Entry mengikuti arah tren pada break Donchian M15-20 + body close di luar level.

Setup:
- BUY (H1 UP): Close[-2] > max(High 20 bar) + Close > EMA21 + RSI >= 55
  + SL = min(Low 3 bar) - 1.00. TP 1:2.
- SELL (H1 DOWN): mirror (break low, RSI <= 45).
- RSI satu arah saja (tanpa cap atas/bawah): runaway jenuh (RSI 100/0)
  justru ciri breakout; filter kejenuhan diwakilkan Donchian + body close.
Window 14:00-02:00 WIB (sama seperti pullback).
"""
from __future__ import annotations

import pandas as pd

from config import LOCAL_TZ

RSI_PERIOD = 14
SL_BUF, MIN_RISK, RR = 1.0, 0.5, 2.0
DONCHIAN_N = 20
TREND_START_HOUR = 14
TREND_END_HOUR = 2


def _in_window(hour: int) -> bool:
    if TREND_START_HOUR <= hour < 24:
        return True
    return 0 <= hour < TREND_END_HOUR


def _add(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "EMA21" not in df.columns:
        df["EMA21"] = df["close"].ewm(span=21, adjust=False).mean()
    if "RSI" not in df.columns:
        delta = df["close"].diff()
        gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
        ag = gain.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
        al = loss.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
        df["RSI"] = 100 - (100 / (1 + ag / al))
    return df


def is_runaway(m15_df, direction: str, threshold: float = 3.00) -> bool:
    """Harga extended dari zona EMA = pullback kecil kemungkinan datang cepat."""
    try:
        df = _add(m15_df)
        c = df.iloc[-2]
        close = float(c["close"])
        zone_hi = max(float(c["EMA21"]), float(c.get("EMA50", c["EMA21"])))
        zone_lo = min(float(c["EMA21"]), float(c.get("EMA50", c["EMA21"])))
        if direction == "UP":
            return close - zone_hi > threshold
        if direction == "DOWN":
            return zone_lo - close > threshold
    except Exception:
        return False
    return False


def evaluate(h1_df, m15_df, h1_trend=None, now=None):
    """Return (signal, entry, sl, tp, reason). Butuh H1 trend UP/DOWN."""
    if m15_df is None or len(m15_df) < 40:
        return None, 0, 0, 0, "Breakout batal: M15 kurang"
    closed_time = pd.to_datetime(
        m15_df.iloc[-2]["time"], unit="s", utc=True).tz_convert(LOCAL_TZ)
    from datetime import datetime as _dt

    hour = closed_time.hour if now is None else now.hour
    if not _in_window(hour):
        return None, 0, 0, 0, "Di luar jam breakout 14-02 WIB"
    direction = h1_trend
    if direction not in ("UP", "DOWN"):
        try:
            from strategies.regime_classifier import classify_regime
            direction = classify_regime(h1_df).get("trend")
        except Exception:
            direction = None
    if direction not in ("UP", "DOWN"):
        return None, 0, 0, 0, "Breakout tahan: arah H1 tak jelas"
    df = _add(m15_df)
    if "EMA50" not in df.columns:
        df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
    closed = df.iloc[-2]
    try:
        c, o = float(closed["close"]), float(closed["open"])
        e21, rc = float(closed["EMA21"]), float(closed["RSI"])
    except Exception:
        return None, 0, 0, 0, "Breakout batal: indikator belum matang"
    import math
    if math.isnan(e21) or math.isnan(rc):
        return None, 0, 0, 0, "Breakout batal: indikator belum matang"
    # Donchian dari bar closed (kecualikan [-1] forming dan [-2] sinyal)
    try:
        window = df.iloc[-2 - DONCHIAN_N:-2]
        dc_high, dc_low = float(window["high"].max()), float(window["low"].min())
    except Exception:
        return None, 0, 0, 0, "Breakout batal: window Donchian kurang"

    if direction == "UP":
        if not (c > dc_high and c > o and c > e21):
            return None, 0, 0, 0, f"Breakout tahan: belum break high20 ({dc_high:.1f})"
        if not (rc >= 55):
            return None, 0, 0, 0, f"Breakout BUY batal: RSI {rc:.1f} < 55"
        sl = min(float(df.iloc[-2]["low"]), float(df.iloc[-3]["low"]),
                 float(df.iloc[-4]["low"])) - SL_BUF
        if c - sl < MIN_RISK:
            return None, 0, 0, 0, "Breakout BUY batal: SL terlalu rapat"
        return "BUY", c, sl, c + (c - sl) * RR, \
            f"H1 UP Breakout high20 {dc_high:.1f} + RSI {rc:.1f} + Runaway"

    if not (c < dc_low and c < o and c < e21):
        return None, 0, 0, 0, f"Breakout tahan: belum break low20 ({dc_low:.1f})"
    if not (rc <= 45):
        return None, 0, 0, 0, f"Breakout SELL batal: RSI {rc:.1f} > 45"
    sl = max(float(df.iloc[-2]["high"]), float(df.iloc[-3]["high"]),
             float(df.iloc[-4]["high"])) + SL_BUF
    if sl - c < MIN_RISK:
        return None, 0, 0, 0, "Breakout SELL batal: SL terlalu rapat"
    return "SELL", c, sl, c - (sl - c) * RR, \
        f"H1 DOWN Breakout low20 {dc_low:.1f} + RSI {rc:.1f} + Runaway"
