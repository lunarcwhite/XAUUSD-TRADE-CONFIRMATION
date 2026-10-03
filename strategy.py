"""Session Liquidity Sweep + EMA 50 / RSI 14 (PRD S4.2)."""
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
    """Sweep Asian High/Low + rejection + candle + RSI hook + EMA50. Evaluasi candle [-2]."""
    closed_time = pd.to_datetime(
        df.iloc[-2]["time"], unit="s", utc=True).tz_convert(LOCAL_TZ)
    exec_hour = closed_time.hour if now is None else now.hour
    if not (EXEC_START_HOUR <= exec_hour < EXEC_END_HOUR):
        return None, 0, 0, 0, "Di luar jam aktif London/NY"
    if len(df) < 60:
        return None, 0, 0, 0, "Data kurang (EMA50 belum matang)"
    ah, al = get_asian_range(df, ref_date=closed_time.date())
    if ah is None:
        return None, 0, 0, 0, "Range Asia belum lengkap"

    closed, prev = df.iloc[-2], df.iloc[-3]
    if pd.isna(closed["EMA50"]) or pd.isna(closed["RSI"]):
        return None, 0, 0, 0, "Indikator belum matang"
    # ponytail: "sempat" = max 5 bar terakhir; upgrade ke swing scanner bila sinyal kurang.
    recent = df["RSI"].iloc[-6:-1]
    c, o, ema = closed["close"], closed["open"], closed["EMA50"]
    rc, rp = closed["RSI"], prev["RSI"]

    if (closed["high"] > ah or prev["high"] > ah) and c < ah:  # SELL sweep atas
        if c >= o:
            return None, 0, 0, 0, "SELL batal: close bukan bearish"
        if not (recent.max() >= RSI_OB and rc < rp):
            return None, 0, 0, 0, f"SELL batal: RSI tak konfirmasi ({rc:.1f})"
        if c > ema:
            return None, 0, 0, 0, f"SELL batal: harga di atas EMA50 ({ema:.2f})"
        sl = max(closed["high"], prev["high"]) + SL_BUF
        if sl - c < MIN_RISK:
            return None, 0, 0, 0, "SELL batal: SL terlalu rapat"
        return "SELL", c, sl, c - (sl - c) * RR, f"Asian High Sweep + RSI ({rc:.1f}) + Under EMA50"

    if (closed["low"] < al or prev["low"] < al) and c > al:  # BUY sweep bawah
        if c <= o:
            return None, 0, 0, 0, "BUY batal: close bukan bullish"
        if not (recent.min() <= RSI_OS and rc > rp):
            return None, 0, 0, 0, f"BUY batal: RSI tak konfirmasi ({rc:.1f})"
        if c < ema:
            return None, 0, 0, 0, f"BUY batal: harga di bawah EMA50 ({ema:.2f})"
        sl = min(closed["low"], prev["low"]) - SL_BUF
        if c - sl < MIN_RISK:
            return None, 0, 0, 0, "BUY batal: SL terlalu rapat"
        return "BUY", c, sl, c + (c - sl) * RR, f"Asian Low Sweep + RSI ({rc:.1f}) + Over EMA50"

    return None, 0, 0, 0, "Tidak ada sweep valid"


evaluate_session_strategy_with_confluence = evaluate_session_strategy  # kompat nama lama


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
