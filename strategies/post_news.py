"""Strategi C: Post-News Momentum Tertunda (cabang ke-3).

Aktif hanya dalam jendela H+30 s/d H+120 menit setelah berita USD High Impact
(blackout +-30 mnt tetap blokir di main; strategi ini mulai SETELAH blackout).
Prinsip: jangan kejar spike pertama; ambil lanjutan arah yang terkonfirmasi.

Setup (M15, tanpa batas jam — news itu sendiri penanda waktu):
- BUY: 3 close terakhir naik + Close[-2] > EMA21 + RSI[-2] > 50
  + range bar ekspansi (>= $1.50) + SL = min(Low 3 bar) - 1.50.
- SELL: mirror (3 close turun, Close < EMA21, RSI < 50, SL + 1.50).
- TP 1:2. Risiko 0.5x (diwujudkan via risk_mult=0.5 di router/broadcast).
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from config import LOCAL_TZ

from strategies.indicators import atr_value, sl_buffer

RSI_PERIOD = 14
SL_BUF_NEWS, MIN_RISK, RR = 1.50, 0.75, 2.0
POST_MIN_START, POST_MIN_END = 30, 120
MIN_EXPANSION = 1.50


def _event_time(ev):
    if "time_wib" in ev:
        return ev["time_wib"]
    try:
        return datetime.fromisoformat(ev["date"]).astimezone(LOCAL_TZ)
    except Exception:
        return None


def minutes_since_last_event(events, now=None) -> tuple[float | None, str | None]:
    """Jarak menit dari event terakhir yang sudah lewat. (None, None) bila tak ada."""
    now = now or datetime.now(LOCAL_TZ)
    best, title = None, None
    for ev in events or []:
        try:
            if "time_wib" not in ev and (
                ev.get("country") != "USD" or ev.get("impact") != "High" or not ev.get("date")
            ):
                continue
            t = _event_time(ev)
            if t is None or t > now:
                continue
            dt = (now - t).total_seconds() / 60.0
            if best is None or dt < best:
                best, title = dt, ev.get("title", "-")
        except Exception:
            continue
    return best, title


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


def evaluate(m15_df, events=None, now=None):
    """Return (signal, entry, sl, tp, reason). Hanya dalam jendela post-news."""
    now = now or datetime.now(LOCAL_TZ)
    dt_min, title = minutes_since_last_event(events, now)
    if dt_min is None:
        return None, 0, 0, 0, "Post-news nonaktif: tak ada event lampau"
    if not (POST_MIN_START < dt_min <= POST_MIN_END):
        return None, 0, 0, 0, f"Di luar jendela post-news (H+{dt_min:.0f}m dari {title})"
    if m15_df is None or len(m15_df) < 60:
        return None, 0, 0, 0, "Post-news batal: M15 kurang"
    df = _add(m15_df)
    buf = sl_buffer(atr_value(df), floor=SL_BUF_NEWS)  # SL lebar news, ikut ATR
    c1, c2, c3 = df.iloc[-2], df.iloc[-3], df.iloc[-4]
    try:
        closes = [float(c3["close"]), float(c2["close"]), float(c1["close"])]
        rc = float(c1["RSI"])
        e21 = float(c1["EMA21"])
        entry = float(c1["close"])
        rng = float(c1["high"]) - float(c1["low"])
    except Exception:
        return None, 0, 0, 0, "Post-news batal: indikator belum matang"
    import math
    if math.isnan(rc) or math.isnan(e21):
        return None, 0, 0, 0, "Post-news batal: indikator belum matang"
    if rng < MIN_EXPANSION:
        return None, 0, 0, 0, f"Post-news tahan: ekspansi tipis (${rng:.2f}<${MIN_EXPANSION})"
    # BUY: momentum naik beruntun + di atas EMA21 + RSI>50 + candle bullish
    if closes[0] < closes[1] < closes[2] and entry > e21 and rc > 50 \
            and float(c1["close"]) > float(c1["open"]):
        sl = min(float(c1["low"]), float(c2["low"]), float(c3["low"])) - buf
        if entry - sl < MIN_RISK:
            return None, 0, 0, 0, "Post-news BUY batal: SL terlalu rapat"
        tp = entry + (entry - sl) * RR
        return "BUY", entry, sl, tp, \
            f"Post-news {title} H+{dt_min:.0f}m + 3 bar naik + RSI {rc:.1f} (SL lebar, risk 0.5x)"
    # SELL: mirror
    if closes[0] > closes[1] > closes[2] and entry < e21 and rc < 50 \
            and float(c1["close"]) < float(c1["open"]):
        sl = max(float(c1["high"]), float(c2["high"]), float(c3["high"])) + buf
        if sl - entry < MIN_RISK:
            return None, 0, 0, 0, "Post-news SELL batal: SL terlalu rapat"
        tp = entry - (sl - entry) * RR
        return "SELL", entry, sl, tp, \
            f"Post-news {title} H+{dt_min:.0f}m + 3 bar turun + RSI {rc:.1f} (SL lebar, risk 0.5x)"
    return None, 0, 0, 0, f"Post-news tahan: momentum belum searah (RSI {rc:.1f})"
