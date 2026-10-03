"""Market Regime Classifier H1: ADX14 + EMA50/200 (PRD V2 S4.2, pure pandas)."""
from __future__ import annotations

import pandas as pd

ADX_PERIOD = 14
ADX_THRESHOLD = 25.0


def add_h1_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """EMA50, EMA200, ADX14 Wilder via ewm(alpha=1/14). Butuh >= ~30 bar, ideal 210+."""
    df = df.copy()
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["EMA200"] = df["close"].ewm(span=200, adjust=False).mean()
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    prev_high, prev_low = high.shift(1), low.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move.clip(lower=0)
    minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move.clip(lower=0)
    alpha = 1 / ADX_PERIOD
    atr = tr.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    df["ADX"] = dx.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean()
    df["PLUS_DI"] = plus_di
    df["MINUS_DI"] = minus_di
    return df


def classify_regime(h1_df) -> dict:
    """Klasifikasi pada bar closed [-2]. Return {regime, adx, trend, reason}.

    TRENDING: ADX>=25 DAN (Close>EMA50>EMA200 | Close<EMA50<EMA200).
    RANGING: ADX<25 ATAU EMA entangled/flat / data kurang.
    """
    if h1_df is None or len(h1_df) < 60:
        return {"regime": "RANGING", "adx": 0.0, "trend": None,
                "reason": "H1 kurang (<60), fallback RANGING"}
    h1 = add_h1_indicators(h1_df) if "ADX" not in h1_df.columns else h1_df
    try:
        c = h1.iloc[-2]
        adx = float(c.get("ADX", float("nan")))
        close, e50, e200 = float(c["close"]), float(c["EMA50"]), float(c["EMA200"])
    except Exception:
        return {"regime": "RANGING", "adx": 0.0, "trend": None,
                "reason": "Indikator H1 belum matang, fallback RANGING"}
    import math
    if math.isnan(adx):
        # ADX butuh 2*period; fallback: EMA stack saja, anggap ranging agar aman.
        return {"regime": "RANGING", "adx": 0.0, "trend": None,
                "reason": "ADX belum matang, fallback RANGING"}
    if adx >= ADX_THRESHOLD and close > e50 > e200:
        return {"regime": "TRENDING", "adx": adx, "trend": "UP",
                "reason": f"ADX {adx:.1f}>=25 + Close>EMA50>EMA200"}
    if adx >= ADX_THRESHOLD and close < e50 < e200:
        return {"regime": "TRENDING", "adx": adx, "trend": "DOWN",
                "reason": f"ADX {adx:.1f}>=25 + Close<EMA50<EMA200"}
    if adx < ADX_THRESHOLD:
        return {"regime": "RANGING", "adx": adx, "trend": None,
                "reason": f"ADX {adx:.1f}<25 konsolidasi"}
    return {"regime": "RANGING", "adx": adx, "trend": None,
            "reason": f"ADX {adx:.1f} tapi EMA entangled"}
