"""Kompat shim: logika strategi tinggal di strategies/* (PRD V2).

File ini hanya meneruskan nama lama agar import existing tak rusak:
- evaluate_session_strategy -> strategies.session_sweep.evaluate
- add_indicators / get_asian_range -> helper M15 asli (dipakai chart & demo)
- classify_market_regime / evaluate_trend_pullback / route_dual_strategy
  -> dispatcher utama di strategy_router.
MTF 3-lapis lama sudah dihapus (tak ada pemanggil sejak router V2).
"""
from datetime import datetime

import pandas as pd

from config import (
    ASIAN_END_HOUR,
    ASIAN_START_HOUR,
    EXEC_END_HOUR,
    EXEC_START_HOUR,
    LOCAL_TZ,
)

RSI_PERIOD = 14
RSI_OS, RSI_OB = 35, 65
SL_BUF, MIN_RISK, RR = 1.0, 0.5, 2.0


def add_indicators(df):
    """EMA 50 + RSI 14 Wilder (alpha=1/14) via pandas."""
    df = df.copy()
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
    delta = df["close"].diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    df["RSI"] = 100 - (100 / (1 + avg_gain / avg_loss))
    return df


def get_asian_range(df, ref_date=None):
    """High/Low sesi Asia 06:00-14:00 WIB pada tanggal bar acuan (default: candle [-2])."""
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


def evaluate_session_strategy(df, now=None):
    """Delegasi ke strategies.session_sweep.evaluate (sumber tunggal)."""
    from strategies.session_sweep import evaluate as _e
    return _e(df, now=now)


evaluate_session_strategy_with_confluence = evaluate_session_strategy  # kompat nama lama


def classify_market_regime(h1_df):
    """Kompat: klasifikasi H1 via strategies.regime_classifier."""
    from strategies.regime_classifier import classify_regime as _c
    return _c(h1_df)


def evaluate_trend_pullback(h1_df, m15_df, h1_trend=None, now=None):
    """Kompat: pullback via strategies.trend_pullback."""
    from strategies.trend_pullback import evaluate as _e
    return _e(h1_df, m15_df, h1_trend=h1_trend, now=now)


def route_dual_strategy(h1_df, m15_df, h1_trend_override=None, events=None, now=None):
    """Kompat: routing via strategy_router.route."""
    from strategy_router import route as _r
    return _r(h1_df, m15_df, h1_trend_override=h1_trend_override,
              events=events, now=now)


if __name__ == "__main__":
    import MetaTrader5 as mt5

    from config import SYMBOL

    if not mt5.initialize():
        print("[ERROR] Gagal inisialisasi MT5.")
        raise SystemExit(1)
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, 100)
    if rates is None or len(rates) < 60:
        print("[ERROR] Data M15 tak cukup.")
        raise SystemExit(1)
    df = add_indicators(pd.DataFrame(rates))
    ah, al = get_asian_range(df)
    print(f"Asian High: {ah} | Asian Low: {al}")
    sig, entry, sl, tp, reason = evaluate_session_strategy(df)
    print(f"Sinyal: {sig} | Entry: {entry} SL: {sl} TP: {tp} | {reason}")
    mt5.shutdown()
