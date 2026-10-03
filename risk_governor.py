"""Risk governor anti-overtrading: 1 posisi + cooldown + daily loss limit."""
from __future__ import annotations

import sqlite3
from datetime import datetime


def has_open_position(broker, symbol) -> tuple[bool, str]:
    """True = blokir sinyal (ada posisi ATAU broker tak terjangkau -> fail-closed)."""
    try:
        poss = broker.list_positions(symbol) if symbol else broker.list_positions()
    except Exception as e:
        # Fail-closed: koneksi broker putus -> jangan kirim sinyal baru selagi
        # posisi real mungkin masih terbuka.
        return True, f"broker tak terjangkau ({e})"
    if poss is None:
        return True, "broker tak terjangkau (respon kosong)"
    if poss:
        t = getattr(poss[0], "ticket", "?")
        return True, f"sudah ada {len(poss)} posisi terbuka (#{t})"
    return False, "tanpa posisi"


def cooldown_remaining_minutes(last_ts, cooldown_min: float, now) -> float:
    """Sisa cooldown dalam menit (>0 = masih diblokir). None last = 0."""
    if not last_ts or not cooldown_min or cooldown_min <= 0:
        return 0.0
    try:
        elapsed = (now - last_ts).total_seconds() / 60.0
    except Exception:
        return 0.0
    return max(0.0, float(cooldown_min) - elapsed)


def daily_realized(user_id: str = "default", db_path: str | None = None,
                   day=None) -> float:
    """Jumlah net_profit terealisasi hari ini untuk user. Fail-open 0.0."""
    try:
        from config import DB_NAME, LOCAL_TZ
    except Exception:
        return 0.0
    day = day or datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    path = db_path
    if not path:
        try:
            from config import DB_NAME as _DB
            path = _DB
        except Exception:
            return 0.0
    try:
        conn = sqlite3.connect(path)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_history)")]
            if "user_id" in cols:
                row = conn.execute(
                    "SELECT COALESCE(SUM(net_profit),0) FROM trade_history "
                    "WHERE close_time LIKE ? AND user_id=?",
                    (day + "%", str(user_id))).fetchone()
            else:  # DB lama single-user: semua baris milik user ini
                row = conn.execute(
                    "SELECT COALESCE(SUM(net_profit),0) FROM trade_history "
                    "WHERE close_time LIKE ?", (day + "%",)).fetchone()
        finally:
            conn.close()
        return float((row or [0.0])[0] or 0.0)
    except Exception:
        return 0.0


def daily_loss_breached(user_id, broker, max_loss_pct: float,
                        db_path: str | None = None) -> tuple[bool, float, float]:
    """(breached, realized, limit). Limit = balance * pct. Fail-open False."""
    try:
        pct = float(max_loss_pct or 0)
    except Exception:
        return False, 0.0, 0.0
    if pct <= 0:
        return False, 0.0, 0.0
    try:
        balance = float(broker.get_balance() or 0.0)
    except Exception:
        return False, 0.0, 0.0
    if balance <= 0:
        return False, 0.0, 0.0
    realized = daily_realized(user_id, db_path)
    limit = round(balance * pct, 2)
    return (realized <= -limit, realized, limit)


def eligible(session, last_ts, now, cooldown_min: float = 60,
             max_loss_pct: float = 0.03,
             db_path: str | None = None) -> tuple[bool, str]:
    """Cek lengkap per-session. Return (ok, reason)."""
    user, broker = session.get("user", {}), session.get("broker")
    uid = str(user.get("id") or "?")
    sym = session.get("symbol")
    blocked, detail = has_open_position(broker, sym)
    if blocked:
        return False, f"GOV {uid}: tahan maks-1-posisi ({detail})"
    remain = cooldown_remaining_minutes(last_ts, cooldown_min, now)
    if remain > 0:
        return False, f"GOV {uid}: cooldown {remain:.0f}m lagi"
    breached, realized, limit = daily_loss_breached(
        uid, broker, max_loss_pct, db_path)
    if breached:
        return False, f"GOV {uid}: daily-loss {realized:+.2f} <= -{limit:.2f}"
    return True, "ok"
