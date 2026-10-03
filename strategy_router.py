"""Dispatcher 4-cabang: post-news > sweep / pullback / breakout (PRD V2+).

Prioritas:
1. POST_NEWS (H+30 s/d H+120 mnt pasca USD High Impact) — eksklusif, risk 0.5x.
2. RANGING -> session_sweep (14-23 WIB).
3. TRENDING -> trend_pullback dulu; bila runaway (belum sentuh zona) -> breakout.
Blackout +-30 mnt tetap diblokir di main sebelum router dipanggil.
"""
from __future__ import annotations

from strategies.breakout import evaluate as evaluate_breakout
from strategies.post_news import evaluate as evaluate_post_news
from strategies.post_news import minutes_since_last_event
from strategies.regime_classifier import classify_regime
from strategies.session_sweep import evaluate as evaluate_sweep
from strategies.trend_pullback import evaluate as evaluate_pullback

POST_MIN_START, POST_MIN_END = 30, 120


def _in_post_window(events, now) -> bool:
    try:
        dt_min, _ = minutes_since_last_event(events, now)
    except Exception:
        return False
    return dt_min is not None and POST_MIN_START < dt_min <= POST_MIN_END


def route(h1_df, m15_df, h1_trend_override=None, events=None, now=None):
    """Return (signal, entry, sl, tp, reason, regime, strategy, risk_mult).

    risk_mult 0.5 untuk post-news (SL lebar), 1.0 untuk lainnya.
    reason selalu diawali [REZIM] agar traceable di log & Telegram.
    """
    from datetime import datetime as _dt

    try:
        from config import LOCAL_TZ as _TZ
        now = now or _dt.now(_TZ)
    except Exception:
        pass
    # Cabang 1: post-news eksklusif dalam jendela
    if events is not None and _in_post_window(events, now):
        sig, entry, sl, tp, rs = evaluate_post_news(m15_df, events, now)
        tag = f"[POST_NEWS H+] POSTNEWS"
        return sig, entry, sl, tp, f"{tag}: {rs}", "POST_NEWS", "post_news", 0.5
    info = classify_regime(h1_df)
    regime, adx = info.get("regime", "RANGING"), float(info.get("adx") or 0.0)
    trend = h1_trend_override or info.get("trend")
    if regime == "TRENDING":
        sig, entry, sl, tp, rs = evaluate_pullback(h1_df, m15_df, h1_trend=trend)
        if sig:
            tag = f"[TRENDING ADX {adx:.1f} {trend}] PULLBACK"
            return sig, entry, sl, tp, f"{tag}: {rs}", regime, "trend_pullback", 1.0
        # Fallback runaway -> breakout (hanya bila pullback gagal karena belum sentuh zona)
        if "belum sentuh" in rs:
            b_sig, b_e, b_sl, b_tp, b_rs = evaluate_breakout(
                h1_df, m15_df, h1_trend=trend)
            if b_sig:
                tag = f"[TRENDING ADX {adx:.1f} {trend}] BREAKOUT"
                return b_sig, b_e, b_sl, b_tp, f"{tag}: {b_rs}", regime, "breakout", 1.0
        tag = f"[TRENDING ADX {adx:.1f} {trend}] PULLBACK"
        return sig, entry, sl, tp, f"{tag}: {rs}", regime, "trend_pullback", 1.0
    sig, entry, sl, tp, rs = evaluate_sweep(m15_df)
    tag = f"[RANGING ADX {adx:.1f}] SWEEP"
    return sig, entry, sl, tp, f"{tag}: {rs}", regime, "session_sweep", 1.0
