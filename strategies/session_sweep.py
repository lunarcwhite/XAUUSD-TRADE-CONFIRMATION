"""Strategi A: Session Liquidity Sweep (aktif saat RANGING, PRD V2 S4.3)."""
from __future__ import annotations

import pandas as pd

from config import (
    ASIAN_END_HOUR,
    ASIAN_START_HOUR,
    EXEC_END_HOUR,
    EXEC_START_HOUR,
    LOCAL_TZ,
)

from strategies.indicators import atr_value, sl_buffer

RSI_PERIOD = 14
RSI_OS, RSI_OB = 35, 65
SL_BUF, MIN_RISK, RR = 1.0, 0.5, 2.0


def _add_rsi(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "EMA50" not in df.columns:
        df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
    if "RSI" not in df.columns:
        delta = df["close"].diff()
        gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
        ag = gain.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
        al = loss.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
        df["RSI"] = 100 - (100 / (1 + ag / al))
    return df


def get_asian_range(df, ref_date=None):
    dt = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(LOCAL_TZ)
    if ref_date is None:
        ref_ts = df.iloc[-2]["time"] if len(df) >= 2 else df.iloc[-1]["time"]
        ref_date = pd.to_datetime(ref_ts, unit="s", utc=True).tz_convert(LOCAL_TZ).date()
    asian = df[
        (dt.dt.date == ref_date)
        & (dt.dt.hour >= ASIAN_START_HOUR)
        & (dt.dt.hour < ASIAN_END_HOUR)
    ]
    if len(asian) < 4:
        return None, None
    return asian["high"].max(), asian["low"].min()


def evaluate(m15_df, now=None):
    """Return (signal, entry, sl, tp, reason). Window 14:00-23:00 WIB."""
    if m15_df is None or len(m15_df) < 60:
        return None, 0, 0, 0, "Sweep batal: M15 kurang"
    closed_time = pd.to_datetime(
        m15_df.iloc[-2]["time"], unit="s", utc=True).tz_convert(LOCAL_TZ)
    exec_hour = closed_time.hour if now is None else now.hour
    if not (EXEC_START_HOUR <= exec_hour < EXEC_END_HOUR):
        return None, 0, 0, 0, "Di luar jam sweep 14-23 WIB"
    df = _add_rsi(m15_df)
    buf = sl_buffer(atr_value(df))  # ATR-based: SL di luar noise, min $1.00
    ah, al = get_asian_range(df, ref_date=closed_time.date())
    if ah is None:
        return None, 0, 0, 0, "Range Asia belum lengkap"
    closed, prev = df.iloc[-2], df.iloc[-3]
    if pd.isna(closed["EMA50"]) or pd.isna(closed["RSI"]):
        return None, 0, 0, 0, "Indikator belum matang"
    recent = df["RSI"].iloc[-6:-1]
    c, o, ema = closed["close"], closed["open"], closed["EMA50"]
    rc, rp = closed["RSI"], prev["RSI"]

    if (closed["high"] > ah or prev["high"] > ah) and c < ah:
        if c >= o:
            return None, 0, 0, 0, "SELL batal: close bukan bearish"
        if not (recent.max() >= RSI_OB and rc < rp):
            return None, 0, 0, 0, f"SELL batal: RSI tak konfirmasi ({rc:.1f})"
        if c > ema:
            return None, 0, 0, 0, f"SELL batal: harga di atas EMA50 ({ema:.2f})"
        sl = max(closed["high"], prev["high"]) + buf
        if sl - c < MIN_RISK:
            return None, 0, 0, 0, "SELL batal: SL terlalu rapat"
        return "SELL", float(c), float(sl), float(c - (sl - c) * RR), \
            f"Asian High Sweep + RSI ({rc:.1f}) + Under EMA50"

    if (closed["low"] < al or prev["low"] < al) and c > al:
        if c <= o:
            return None, 0, 0, 0, "BUY batal: close bukan bullish"
        if not (recent.min() <= RSI_OS and rc > rp):
            return None, 0, 0, 0, f"BUY batal: RSI tak konfirmasi ({rc:.1f})"
        if c < ema:
            return None, 0, 0, 0, f"BUY batal: harga di bawah EMA50 ({ema:.2f})"
        sl = min(closed["low"], prev["low"]) - buf
        if c - sl < MIN_RISK:
            return None, 0, 0, 0, "BUY batal: SL terlalu rapat"
        return "BUY", float(c), float(sl), float(c + (c - sl) * RR), \
            f"Asian Low Sweep + RSI ({rc:.1f}) + Over EMA50"

    return None, 0, 0, 0, "Tidak ada sweep valid"
