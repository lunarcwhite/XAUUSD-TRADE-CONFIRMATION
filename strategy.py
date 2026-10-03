"""Session Liquidity Sweep + EMA 50 / RSI 14 + MTF D1/H4/H1/M5 (SMC)."""
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


# ================= MTF: Liquidity Sweep 3 lapis =================
# Lapis 1: D1+H4 arah tren (HH/HL + EMA50). Lapis 2: H1 zona + Asian range.
# Lapis 3: M5 sweep + PinBar/Engulfing/Close-body. News & risk tetap (±30mnt, 1:2).

def _closed(df):
    """DataFrame bar closed saja (buang bar forming [-1] bila len>=2)."""
    if df is None or len(df) < 3:
        return df
    return df.iloc[:-1]


def _h4_structure(h4):
    """HH/HL vs LH/LL dari 30 bar closed terakhir (2x15). Return bull_struct, bear_struct."""
    c = _closed(h4)
    if c is None or len(c) < 30:
        return False, False
    tail = c.tail(30)
    first, last = tail.iloc[:15], tail.iloc[15:]
    try:
        hh = float(last["high"].max()) > float(first["high"].max())
        hl = float(last["low"].min()) > float(first["low"].min())
        lh = float(last["high"].max()) < float(first["high"].max())
        ll = float(last["low"].min()) < float(first["low"].min())
    except Exception:
        return False, False
    return (hh and hl), (lh and ll)


def detect_trend_d1_h4(d1_df, h4_df):
    """Return (trend, reason). trend: BULLISH/BEARISH/None. Syarat EMA + struktur + D1 selaras."""
    if h4_df is None or len(h4_df) < 55:
        return None, "MTF batal: H4 kurang (<55)"
    if d1_df is None or len(d1_df) < 55:
        return None, "MTF batal: D1 kurang (<55)"
    try:
        h4 = add_indicators(h4_df)
        d1 = add_indicators(d1_df)
    except Exception as e:
        return None, f"MTF batal: indikator gagal ({e})"
    try:
        hc, he = float(h4.iloc[-2]["close"]), float(h4.iloc[-2]["EMA50"])
        dc, de = float(d1.iloc[-2]["close"]), float(d1.iloc[-2]["EMA50"])
    except Exception:
        return None, "MTF batal: indikator belum matang"
    import math
    if math.isnan(he) or math.isnan(de):
        return None, "MTF batal: EMA belum matang"
    bull_s, bear_s = _h4_structure(h4)
    h4_bull_ema = hc > he
    h4_bear_ema = hc < he
    d1_bull = dc > de
    d1_bear = dc < de
    if h4_bull_ema and d1_bull and bull_s:
        return "BULLISH", f"H4 HH/HL + H4>D1 EMA (H4 {hc:.1f}>{he:.1f})"
    if h4_bear_ema and d1_bear and bear_s:
        return "BEARISH", f"H4 LH/LL + H4<D1 EMA (H4 {hc:.1f}<{he:.1f})"
    # EMA selaras tapi struktur ranging -> tahan (jangan lawan arus tak jelas).
    if h4_bull_ema and d1_bull:
        return None, "MTF tahan: EMA bullish tapi struktur H4 ranging"
    if h4_bear_ema and d1_bear:
        return None, "MTF tahan: EMA bearish tapi struktur H4 ranging"
    return None, "MTF batal: D1/H4 tak selaras"


def get_key_zones(h1_df, m15_df):
    """Resistance/support dari H1 48 bar + Asian High/Low. Return dict."""
    res = sup = None
    try:
        if h1_df is not None and len(h1_df) >= 10:
            hc = _closed(h1_df).tail(48)
            res, sup = float(hc["high"].max()), float(hc["low"].min())
    except Exception:
        pass
    try:
        ah, al = get_asian_range(m15_df)
    except Exception:
        ah, al = None, None
    if ah is not None:
        res = ah if res is None else max(res, float(ah))
    if al is not None:
        sup = al if sup is None else min(sup, float(al))
    return {"resistance": res, "support": sup,
            "h1_high": res, "h1_low": sup, "asian_high": ah, "asian_low": al}


def _body(o, c):
    return abs(float(c) - float(o))


def is_bullish_pinbar(o, h, low, c):
    try:
        b = _body(o, c)
        if b <= 0 or not (c > o):
            return False
        lower = min(float(o), float(c)) - float(low)
        upper = float(h) - max(float(o), float(c))
        return lower > 2 * b and upper < b * 1.5
    except Exception:
        return False


def is_bearish_pinbar(o, h, low, c):
    try:
        b = _body(o, c)
        if b <= 0 or not (c < o):
            return False
        upper = float(h) - max(float(o), float(c))
        lower = min(float(o), float(c)) - float(low)
        return upper > 2 * b and lower < b * 1.5
    except Exception:
        return False


def is_bullish_engulfing(p_o, p_c, o, c):
    try:
        return (p_c < p_o) and (c > o) and (o <= p_c) and (c >= p_o)
    except Exception:
        return False


def is_bearish_engulfing(p_o, p_c, o, c):
    try:
        return (p_c > p_o) and (c < o) and (o >= p_c) and (c <= p_o)
    except Exception:
        return False


def detect_m5_confirmation(m5_df, side, zone_price):
    """Sweep M5 + PA. Return (ok, pa_name, entry_ref). entry_ref = close[-2]."""
    if m5_df is None or len(m5_df) < 5 or zone_price is None:
        return False, "M5 tak cukup", 0
    try:
        c2, c3 = m5_df.iloc[-2], m5_df.iloc[-3]
        o2, h2, l2, cl2 = c2["open"], c2["high"], c2["low"], c2["close"]
        o3, cl3 = c3["open"], c3["close"]
    except Exception:
        return False, "M5 rusak", 0
    z = float(zone_price)
    if side == "BUY":
        swept = (float(l2) < z) or (float(m5_df.iloc[-3]["low"]) < z)
        if not swept:
            return False, "M5 belum sweep support", 0
        if not (float(cl2) > z):
            return False, "M5 belum rejection", 0
        if is_bullish_pinbar(o2, h2, l2, cl2):
            return True, "M5 PinBar bullish", float(cl2)
        if is_bullish_engulfing(o3, cl3, o2, cl2):
            return True, "M5 Engulfing bullish", float(cl2)
        if float(cl2) > z + 0.30 and float(cl2) > float(o2):
            return True, "M5 Close-body > zona", float(cl2)
        return False, "M5 tanpa PA bullish", 0
    else:  # SELL
        swept = (float(h2) > z) or (float(m5_df.iloc[-3]["high"]) > z)
        if not swept:
            return False, "M5 belum sweep resistance", 0
        if not (float(cl2) < z):
            return False, "M5 belum rejection", 0
        if is_bearish_pinbar(o2, h2, l2, cl2):
            return True, "M5 PinBar bearish", float(cl2)
        if is_bearish_engulfing(o3, cl3, o2, cl2):
            return True, "M5 Engulfing bearish", float(cl2)
        if float(cl2) < z - 0.30 and float(cl2) < float(o2):
            return True, "M5 Close-body < zona", float(cl2)
        return False, "M5 tanpa PA bearish", 0


def evaluate_mtf_strategy(d1_df, h4_df, h1_df, m15_df, m5_df=None, now=None):
    """3 lapis: D1/H4 tren -> H1 zona -> M5 sweep+PA + M15 RSI/EMA confluence.

    Entry dari M5 close[-2] (presisi), SL buffer $1.00, TP 1:2 (risk tetap).
    Fallback: bila M5 tak ada, pakai logika M15 sweep lama + filter tren.
    """
    if m15_df is None or len(m15_df) < 60:
        return None, 0, 0, 0, "MTF batal: M15 kurang"
    closed_time = pd.to_datetime(
        m15_df.iloc[-2]["time"], unit="s", utc=True).tz_convert(LOCAL_TZ)
    exec_hour = closed_time.hour if now is None else now.hour
    if not (EXEC_START_HOUR <= exec_hour < EXEC_END_HOUR):
        return None, 0, 0, 0, "Di luar jam aktif London/NY"
    # Indikator M15 (confluence lama)
    m15 = add_indicators(m15_df)
    closed, prev = m15.iloc[-2], m15.iloc[-3]
    if pd.isna(closed["EMA50"]) or pd.isna(closed["RSI"]):
        return None, 0, 0, 0, "Indikator M15 belum matang"
    recent = m15["RSI"].iloc[-6:-1]
    c15, o15, ema15 = closed["close"], closed["open"], closed["EMA50"]
    rc, rp = closed["RSI"], prev["RSI"]

    # Lapis 1: tren besar
    trend, trend_reason = detect_trend_d1_h4(d1_df, h4_df)
    if trend is None:
        return None, 0, 0, 0, trend_reason

    # Lapis 2: zona
    zones = get_key_zones(h1_df, m15_df)
    sup, res = zones.get("support"), zones.get("resistance")
    if sup is None or res is None:
        return None, 0, 0, 0, "MTF batal: zona H1/Asia belum lengkap"

    # Lapis 3 + confluence per arah tren (jangan lawan tren)
    # Catatan: MTF pakai momentum searah tren (RSI>50 & naik / <50 & turun),
    # bukan oversold/overbought 35/65 seperti strategi M15 lama (terlalu ketat
    # untuk follow-trend, terbukti via uji sintetis).
    # Toleransi flat 0.5 agar tren kuat jenuh (RSI 100/0) tidak diblok.
    if trend == "BULLISH":
        # Confluence M15 arah buy
        if not (rc + 0.5 >= rp and rc > 50):
            return None, 0, 0, 0, f"MTF tahan: momentum M15 lemah ({rc:.1f})"
        if not (c15 > ema15):
            return None, 0, 0, 0, "MTF tahan: M15 di bawah EMA50"
        if m5_df is None or len(m5_df) < 5:
            # Fallback M15 sweep + tren bullish
            al = zones.get("asian_low") or sup
            if not ((closed["low"] < al or prev["low"] < al) and c15 > al and c15 > o15):
                return None, 0, 0, 0, "MTF tahan: M15 belum sweep (fallback, M5 kosong)"
            sl = min(closed["low"], prev["low"]) - SL_BUF
            entry = float(c15)
        else:
            m5 = add_indicators(m5_df) if "EMA50" not in m5_df.columns else m5_df
            ok, pa, entry_m5 = detect_m5_confirmation(m5, "BUY", sup)
            if not ok:
                return None, 0, 0, 0, f"BUY batal: {pa}"
            try:
                sl = min(float(m5.iloc[-2]["low"]), float(m5.iloc[-3]["low"])) - SL_BUF
            except Exception:
                return None, 0, 0, 0, "BUY batal: SL M5 gagal"
            entry = float(entry_m5)
        if entry - sl < MIN_RISK:
            return None, 0, 0, 0, "BUY batal: SL terlalu rapat"
        tp = entry + (entry - sl) * RR
        base = f"H4/D1 {trend_reason} + Zona {sup:.2f} + {pa if 'pa' in locals() else 'M15 sweep'} + RSI {rc:.1f}"
        return "BUY", entry, sl, tp, base

    # BEARISH
    if not (rc - 0.5 <= rp and rc < 50):
        return None, 0, 0, 0, f"MTF tahan: momentum M15 lemah ({rc:.1f})"
    if not (c15 < ema15):
        return None, 0, 0, 0, "MTF tahan: M15 di atas EMA50"
    if m5_df is None or len(m5_df) < 5:
        ah = zones.get("asian_high") or res
        if not ((closed["high"] > ah or prev["high"] > ah) and c15 < ah and c15 < o15):
            return None, 0, 0, 0, "MTF tahan: M15 belum sweep (fallback, M5 kosong)"
        sl = max(closed["high"], prev["high"]) + SL_BUF
        entry = float(c15)
    else:
        m5 = add_indicators(m5_df) if "EMA50" not in m5_df.columns else m5_df
        ok, pa, entry_m5 = detect_m5_confirmation(m5, "SELL", res)
        if not ok:
            return None, 0, 0, 0, f"SELL batal: {pa}"
        try:
            sl = max(float(m5.iloc[-2]["high"]), float(m5.iloc[-3]["high"])) + SL_BUF
        except Exception:
            return None, 0, 0, 0, "SELL batal: SL M5 gagal"
        entry = float(entry_m5)
    if sl - entry < MIN_RISK:
        return None, 0, 0, 0, "SELL batal: SL terlalu rapat"
    tp = entry - (sl - entry) * RR
    base = f"H4/D1 {trend_reason} + Zona {res:.2f} + {pa if 'pa' in locals() else 'M15 sweep'} + RSI {rc:.1f}"
    return "SELL", entry, sl, tp, base


# ============ PRD V2: shim dual-strategy (dispatcher utama di strategy_router) ============
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
