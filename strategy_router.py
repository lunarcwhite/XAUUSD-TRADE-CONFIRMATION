"""Dispatcher Dual-Mode: SNIPER (M15) vs INTRADAY (M5) — PRD V3.0 S5 + router V2.

- SNIPER (default): 4-cabang V2 — post-news > sweep / pullback / breakout.
  Window & blackout ditangani strategi + main (blackout +-30 mnt short-circuit
  di main untuk kedua mode).
- INTRADAY: Session VWAP M5 + ATR Rejection (strategies/intraday_vwap_m5).
  Rem harian (kuota 3, kill 2%, cutoff 22:30) dicek via state_manager di main
  sebelum broadcast — router hanya mengevaluasi setup.

Kompat: signature lama route(h1, m15, trend_or_events, events_or_now, now)
tetap didukung (deteksi events list yang nyasar ke h1_trend_override —
bug historis main.py). Panggilan baru disarankan pakai keyword:
route(h1, m15, events=..., now=..., mode=..., m5_df=...).
"""
from __future__ import annotations

from strategies.breakout import evaluate as evaluate_breakout
from strategies.post_news import evaluate as evaluate_post_news
from strategies.post_news import minutes_since_last_event
from strategies.regime_classifier import classify_regime
from strategies.session_sweep import evaluate as evaluate_sweep
from strategies.trend_pullback import evaluate as evaluate_pullback

try:
    from config import MODE_INTRADAY, MODE_SNIPER
except Exception:
    MODE_SNIPER, MODE_INTRADAY = "SNIPER", "INTRADAY"

POST_MIN_START, POST_MIN_END = 30, 120


def _in_post_window(events, now) -> bool:
    try:
        dt_min, _ = minutes_since_last_event(events, now)
    except Exception:
        return False
    return dt_min is not None and POST_MIN_START < dt_min <= POST_MIN_END


def _normalize_args(h1_trend_override=None, events=None, now=None):
    """Perbaiki panggilan lama route(h1, m15, events_list, now_dt).

    Bila argumen ke-3 berupa list (events) dan ke-4 datetime -> geser ke slot benar.
    """
    if isinstance(h1_trend_override, (list, tuple)) and events is not None \
            and not isinstance(events, (list, tuple)):
        # pola lama: (events, now) di posisi (trend, events)
        return None, h1_trend_override, events
    return h1_trend_override, events, now


def _route_sniper(h1_df, m15_df, h1_trend_override=None, events=None, now=None):
    """4-cabang V2. Return 8-tuple (signal..risk_mult)."""
    from datetime import datetime as _dt

    try:
        from config import LOCAL_TZ as _TZ
        now = now or _dt.now(_TZ)
    except Exception:
        pass
    # Cabang 1: post-news eksklusif dalam jendela
    if events is not None and _in_post_window(events, now):
        sig, entry, sl, tp, rs = evaluate_post_news(m15_df, events, now)
        tag = "[POST_NEWS H+] POSTNEWS"
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


def _route_intraday(h1_df, m15_df, m5_df, h1_trend_override=None, now=None):
    """Cabang INTRADAY M5. Return 8-tuple; regime='INTRADAY'."""
    from strategies.intraday_vwap_m5 import evaluate as evaluate_intraday

    if m5_df is None or len(m5_df) < 30:
        return (None, 0, 0, 0, "[INTRADAY M5]: M5 kurang untuk VWAP/ATR",
                "INTRADAY", "intraday_vwap_m5", 1.0)
    trend = h1_trend_override
    if trend not in ("UP", "DOWN"):
        try:
            trend = classify_regime(h1_df).get("trend")
        except Exception:
            trend = None
    sig, entry, sl, tp, rs = evaluate_intraday(
        h1_df, m15_df, m5_df, h1_trend=trend, now=now)
    tag = "[INTRADAY M5] VWAP"
    return sig, entry, sl, tp, f"{tag}: {rs}", "INTRADAY", "intraday_vwap_m5", 1.0


def route(h1_df, m15_df, h1_trend_override=None, events=None, now=None,
          mode=None, m5_df=None):
    """Dispatcher mode aktif. Return (signal, entry, sl, tp, reason, regime, strategy, risk_mult).

    mode None -> baca state_manager.bot_state.current_mode (default SNIPER).
    reason selalu diawali tag rezim agar traceable di log & Telegram.
    """
    h1_trend_override, events, now = _normalize_args(
        h1_trend_override, events, now)
    if mode is None:
        try:
            from state_manager import bot_state
            mode = bot_state.current_mode
        except Exception:
            mode = MODE_SNIPER
    mode = str(mode or MODE_SNIPER).upper()
    if mode == MODE_INTRADAY:
        return _route_intraday(h1_df, m15_df, m5_df,
                               h1_trend_override=h1_trend_override, now=now)
    return _route_sniper(h1_df, m15_df, h1_trend_override=h1_trend_override,
                         events=events, now=now)
