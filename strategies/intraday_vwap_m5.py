"""Strategi Mode Intraday M5: Session VWAP + ATR Rejection (PRD V3.0 S5.3).

Setup M5 cepat dengan konteks H1 + area nilai M15:
- BUY: H1 bukan Strong Downtrend + Close[-2] > VWAP[-2] +
  Low[-2]/Low[-3] retest VWAP/Support M15 + Close[-2] > Open[-2].
  SL = Swing Low M5 - 1.2*ATR14, TP = 1:2 R:R.
- SELL: mirror (H1 bukan Strong Uptrend, Close < VWAP, wick high retest,
  close bearish, SL = Swing High + 1.2*ATR).

Pure pandas/numpy (tanpa TA-Lib). Bar acuan = closed [-2] ([-1] forming).
"""
from __future__ import annotations

import math

import pandas as pd

from config import (
    INTRADAY_ATR_PERIOD,
    INTRADAY_MIN_RR,
    INTRADAY_SL_ATR_MULT,
    LOCAL_TZ,
    VWAP_RESET_HOUR,
    is_in_intraday_session,
    is_intraday_entry_closed,
)

from strategies.indicators import atr_series, atr_value

MIN_RISK = 0.50  # SL minimal $0.50 (konsisten sweep/pullback/breakout)
M5_MIN_BARS = 30  # ATR14 + VWAP sesi butuh data cukup
M15_LOOKBACK = 20  # Support/Resistance M15 = Donchian 20 bar
SWING_LOOKBACK = 3  # Swing M5 = min/max Low/High 3 bar closed


def session_vwap_series(m5_df: pd.DataFrame,
                        reset_hour: int = VWAP_RESET_HOUR) -> pd.Series:
    """Session VWAP reset tiap `reset_hour` WIB (pure pandas).

    VWAP = sum(Typical*Vol)/sum(Vol), Typical=(H+L+C)/3, sesi = hari yang
    dimulai `reset_hour` (bar 06:00 ikut sesi baru). Volume: tick_volume,
    fallback volume, else 1. Return Series sejajar index df (NaN bila sesi kosong).
    """
    df = m5_df.copy()
    ts = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(LOCAL_TZ)
    session_day = (ts - pd.Timedelta(hours=reset_hour)).dt.date
    typical = (df["high"].astype(float) + df["low"].astype(float)
               + df["close"].astype(float)) / 3.0
    if "tick_volume" in df.columns:
        vol = df["tick_volume"].astype(float)
    elif "volume" in df.columns:
        vol = df["volume"].astype(float)
    else:
        vol = pd.Series(1.0, index=df.index, dtype=float)
    vol = vol.where((vol > 0) & vol.notna(), 1.0)
    tp_vol = typical * vol
    grp = pd.Series(session_day.values, index=df.index)
    cum_tp = tp_vol.groupby(grp).cumsum()
    cum_vol = vol.groupby(grp).cumsum()
    vwap = cum_tp / cum_vol.replace(0, float("nan"))
    vwap.index = df.index
    return vwap


def m15_support_resistance(m15_df) -> tuple[float | None, float | None]:
    """(support, resistance) = Donchian 20 bar closed M15 (tanpa [-1] forming)."""
    try:
        if m15_df is None or len(m15_df) < 5:
            return None, None
        window = m15_df.iloc[-1 - M15_LOOKBACK:-1] if len(m15_df) > M15_LOOKBACK else m15_df.iloc[:-1]
        return float(window["low"].min()), float(window["high"].max())
    except Exception:
        return None, None


def _h1_strong_trend(h1_df, h1_trend=None) -> str | None:
    """'UP'/'DOWN' bila H1 Strong Trend, else None (fail-open bila data kurang)."""
    if h1_trend in ("UP", "DOWN"):
        return h1_trend
    try:
        if h1_df is None or len(h1_df) < 60:
            return None
        from strategies.regime_classifier import classify_regime
        info = classify_regime(h1_df)
        if info.get("regime") == "TRENDING":
            return info.get("trend")
    except Exception:
        pass
    return None


def get_vwap_atr(m5_df: pd.DataFrame) -> tuple[float, float]:
    """(vwap[-2], atr14[-2]) untuk caption Telegram/chart. NaN bila belum matang."""
    try:
        vwap = float(session_vwap_series(m5_df).iloc[-2])
    except Exception:
        vwap = float("nan")
    try:
        atr_v = float(atr_value(m5_df, period=INTRADAY_ATR_PERIOD))
    except Exception:
        atr_v = float("nan")
    return vwap, atr_v


def evaluate(h1_df, m15_df, m5_df, h1_trend=None, now=None):
    """Return (signal, entry, sl, tp, reason). Entry = Close[-2], TP 1:2 R:R."""
    # 1. Data cukup.
    if m5_df is None or len(m5_df) < M5_MIN_BARS:
        return None, 0, 0, 0, "Intraday batal: M5 kurang (<30)"
    try:
        closed_ts = pd.to_datetime(
            m5_df.iloc[-2]["time"], unit="s", utc=True).tz_convert(LOCAL_TZ)
    except Exception:
        return None, 0, 0, 0, "Intraday batal: time M5 invalid"
    ref_now = now if now is not None else closed_ts

    # 2. Jendela sesi Intraday + cutoff 22:30 (PRD S4 + S5.1).
    try:
        if is_intraday_entry_closed(ref_now):
            return None, 0, 0, 0, "Di luar jam intraday (>= 22:30 WIB)"
        if not is_in_intraday_session(ref_now):
            return None, 0, 0, 0, "Di luar sesi intraday (14:30-17 & 19:30-22:30 WIB)"
    except Exception:
        pass

    # 3. Indikator: VWAP sesi + ATR14 Wilder.
    try:
        vwap_s = session_vwap_series(m5_df)
        vwap = float(vwap_s.iloc[-2])
        atr_v = float(atr_series(m5_df, period=INTRADAY_ATR_PERIOD).iloc[-2])
    except Exception:
        return None, 0, 0, 0, "Intraday batal: VWAP/ATR gagal dihitung"
    if math.isnan(vwap) or math.isnan(atr_v) or atr_v <= 0:
        return None, 0, 0, 0, "Intraday batal: VWAP/ATR belum matang"

    # 4. Konteks H1 (hanya blokir strong trend berlawanan, PRD 5.3).
    strong = _h1_strong_trend(h1_df, h1_trend)

    try:
        c2, c3 = m5_df.iloc[-2], m5_df.iloc[-3]
        close, open_ = float(c2["close"]), float(c2["open"])
        low2, low3 = float(c2["low"]), float(c3["low"])
        high2, high3 = float(c2["high"]), float(c3["high"])
    except Exception:
        return None, 0, 0, 0, "Intraday batal: OHLC M5 invalid"

    support, resistance = m15_support_resistance(m15_df)
    buf = round(INTRADAY_SL_ATR_MULT * atr_v, 2)
    # Toleransi sentuhan wick: di luar noise kecil, max $0.50.
    touch_tol = round(max(0.50, 0.25 * atr_v), 2)

    def _touched_below(level: float | None) -> bool:
        if level is None or math.isnan(level):
            return False
        return (low2 <= level + touch_tol) or (low3 <= level + touch_tol)

    def _touched_above(level: float | None) -> bool:
        if level is None or math.isnan(level):
            return False
        return (high2 >= level - touch_tol) or (high3 >= level - touch_tol)

    # ---- BUY ----
    buy_value_ok = close > vwap
    if buy_value_ok and strong != "DOWN":
        touched = _touched_below(vwap) or _touched_below(support)
        if touched and close > open_:
            swing_low = min(float(m5_df.iloc[-2]["low"]),
                            float(m5_df.iloc[-3]["low"]),
                            float(m5_df.iloc[-4]["low"]))
            sl = round(swing_low - buf, 2)
            risk = round(close - sl, 2)
            if risk < MIN_RISK:
                return None, 0, 0, 0, "Intraday BUY batal: SL terlalu rapat"
            tp = round(close + risk * INTRADAY_MIN_RR, 2)
            return ("BUY", round(close, 2), sl, tp,
                    f"M5 VWAP Rej BUY (VWAP {vwap:.2f} ATR {atr_v:.2f}) + H1 ok")
        if not touched:
            buy_block = f"BUY tahan: wick tak retest VWAP/Support (VWAP {vwap:.2f})"
        else:
            buy_block = "BUY batal: close bukan bullish"
    else:
        if strong == "DOWN":
            buy_block = "BUY tahan: H1 Strong Downtrend"
        else:
            buy_block = f"BUY tahan: close di bawah VWAP ({vwap:.2f})"

    # ---- SELL ----
    sell_value_ok = close < vwap
    if sell_value_ok and strong != "UP":
        touched = _touched_above(vwap) or _touched_above(resistance)
        if touched and close < open_:
            swing_high = max(float(m5_df.iloc[-2]["high"]),
                             float(m5_df.iloc[-3]["high"]),
                             float(m5_df.iloc[-4]["high"]))
            sl = round(swing_high + buf, 2)
            risk = round(sl - close, 2)
            if risk < MIN_RISK:
                return None, 0, 0, 0, "Intraday SELL batal: SL terlalu rapat"
            tp = round(close - risk * INTRADAY_MIN_RR, 2)
            return ("SELL", round(close, 2), sl, tp,
                    f"M5 VWAP Rej SELL (VWAP {vwap:.2f} ATR {atr_v:.2f}) + H1 ok")
        if not touched:
            sell_block = f"SELL tahan: wick tak retest VWAP/Resistance (VWAP {vwap:.2f})"
        else:
            sell_block = "SELL batal: close bukan bearish"
    else:
        if strong == "UP":
            sell_block = "SELL tahan: H1 Strong Uptrend"
        else:
            sell_block = f"SELL tahan: close di atas VWAP ({vwap:.2f})"

    return None, 0, 0, 0, f"Tidak ada setup M5 valid ({buy_block} | {sell_block})"
