"""Strategi B: HTF Trend Pullback (aktif saat TRENDING, PRD V2 S4.3).

Setup M15 koreksi ke zona EMA21-50 searah tren H1:
- BUY: pullback sentuh zona + RSI 40-48 + Close>EMA21.
- SELL: pullback sentuh zona + RSI 52-60 + Close<EMA21.
Window 14:00-02:00 WIB (overnight). SL swing +-1.00, TP 1:2.
"""
from __future__ import annotations

import pandas as pd

from config import LOCAL_TZ

RSI_PERIOD = 14
SL_BUF, MIN_RISK, RR = 1.0, 0.5, 2.0
TREND_START_HOUR = 14
TREND_END_HOUR = 2  # lewat tengah malam


def _in_trend_window(hour: int) -> bool:
    if TREND_START_HOUR <= hour < 24:
        return True
    return 0 <= hour < TREND_END_HOUR


def _add_m15(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "EMA21" not in df.columns:
        df["EMA21"] = df["close"].ewm(span=21, adjust=False).mean()
    if "EMA50" not in df.columns:
        df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
    if "RSI" not in df.columns:
        delta = df["close"].diff()
        gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
        ag = gain.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
        al = loss.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
        df["RSI"] = 100 - (100 / (1 + ag / al))
    return df


def evaluate(h1_df, m15_df, h1_trend=None, now=None):
    """h1_trend: UP/DOWN dari classifier (opsional, dievaluasi ulang bila None)."""
    if m15_df is None or len(m15_df) < 60:
        return None, 0, 0, 0, "Pullback batal: M15 kurang"
    closed_time = pd.to_datetime(
        m15_df.iloc[-2]["time"], unit="s", utc=True).tz_convert(LOCAL_TZ)
    exec_hour = closed_time.hour if now is None else now.hour
    if not _in_trend_window(exec_hour):
        return None, 0, 0, 0, "Di luar jam pullback 14-02 WIB"
    # Arah tren H1 dari EMA stack bila tak diberikan
    direction = h1_trend
    if direction not in ("UP", "DOWN"):
        try:
            from strategies.regime_classifier import classify_regime
            direction = classify_regime(h1_df).get("trend")
        except Exception:
            direction = None
    if direction not in ("UP", "DOWN"):
        return None, 0, 0, 0, "Pullback tahan: arah H1 tak jelas"
    df = _add_m15(m15_df)
    closed, prev = df.iloc[-2], df.iloc[-3]
    if pd.isna(closed["EMA21"]) or pd.isna(closed["EMA50"]) or pd.isna(closed["RSI"]):
        return None, 0, 0, 0, "Indikator M15 belum matang"
    c, o = float(closed["close"]), float(closed["open"])
    e21, e50 = float(closed["EMA21"]), float(closed["EMA50"])
    rc = float(closed["RSI"])
    lo, hi = float(closed["low"]), float(closed["high"])
    plo = float(prev["low"])
    phi = float(prev["high"])

    if direction == "UP":
        zone_lo, zone_hi = min(e21, e50), max(e21, e50)
        touched = (lo <= zone_hi and lo >= zone_lo - 1.5) or \
                  (plo <= zone_hi and plo >= zone_lo - 1.5) or \
                  (lo < zone_lo and c > zone_lo)
        if not touched:
            return None, 0, 0, 0, f"Pullback tahan: belum sentuh EMA21-50 ({zone_lo:.1f}-{zone_hi:.1f})"
        if not (40 <= rc <= 48 or (38 <= rc <= 52 and rc > float(prev["RSI"]))):
            return None, 0, 0, 0, f"BUY batal: RSI {rc:.1f} di luar 40-48"
        if not (c > e21 and c > o):
            return None, 0, 0, 0, "BUY batal: belum rejection > EMA21"
        sl = min(lo, plo) - SL_BUF
        if c - sl < MIN_RISK:
            return None, 0, 0, 0, "BUY batal: SL terlalu rapat"
        return "BUY", c, sl, c + (c - sl) * RR, \
            f"H1 Uptrend Pullback + RSI {rc:.1f} + Rej EMA21"

    # DOWN
    zone_lo, zone_hi = min(e21, e50), max(e21, e50)
    touched = (hi >= zone_lo and hi <= zone_hi + 1.5) or \
              (phi >= zone_lo and phi <= zone_hi + 1.5) or \
              (hi > zone_hi and c < zone_hi)
    if not touched:
        return None, 0, 0, 0, f"Pullback tahan: belum sentuh EMA21-50 ({zone_lo:.1f}-{zone_hi:.1f})"
    if not (52 <= rc <= 60 or (48 <= rc <= 62 and rc < float(prev["RSI"]))):
        return None, 0, 0, 0, f"SELL batal: RSI {rc:.1f} di luar 52-60"
    if not (c < e21 and c < o):
        return None, 0, 0, 0, "SELL batal: belum rejection < EMA21"
    sl = max(hi, phi) + SL_BUF
    if sl - c < MIN_RISK:
        return None, 0, 0, 0, "SELL batal: SL terlalu rapat"
    return "SELL", c, sl, c - (sl - c) * RR, \
        f"H1 Downtrend Pullback + RSI {rc:.1f} + Rej EMA21"
