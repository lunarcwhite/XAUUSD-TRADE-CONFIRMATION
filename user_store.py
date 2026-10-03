"""Penyimpanan user registrasi mandiri (SQLite terpisah dari jurnal trading)."""
from __future__ import annotations

import json
import sqlite3
import time

FIELDS = (
    "chat_id", "user_id", "broker_mode", "risk_percent", "symbol",
    "mt5_login", "mt5_password", "mt5_server", "mt5_path",
    "oanda_api_key", "oanda_account_id", "oanda_env", "oanda_instrument",
    "enabled", "created_at",
)


def _db():
    from config import USERS_DB

    return sqlite3.connect(USERS_DB)


def init_users_db():
    conn = _db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bot_users (
            chat_id TEXT PRIMARY KEY, user_id TEXT, broker_mode TEXT,
            risk_percent REAL, symbol TEXT,
            mt5_login INTEGER, mt5_password TEXT, mt5_server TEXT, mt5_path TEXT,
            oanda_api_key TEXT, oanda_account_id TEXT, oanda_env TEXT,
            oanda_instrument TEXT, enabled INTEGER DEFAULT 1,
            created_at TEXT)"""
    )
    conn.commit()
    conn.close()


def get_user(chat_id):
    init_users_db()
    conn = _db()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM bot_users WHERE chat_id=?",
                       (str(chat_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def save_user(chat_id, **kw):
    """Insert/update per kolom. Return dict user."""
    init_users_db()
    u = get_user(chat_id) or {"chat_id": str(chat_id)}
    u.update(kw)
    u["chat_id"] = str(chat_id)
    cols = [k for k in FIELDS if k in u]
    conn = _db()
    conn.execute(
        f"INSERT OR REPLACE INTO bot_users ({','.join(cols)}) "
        f"VALUES ({','.join('?' for _ in cols)})",
        [u[k] for k in cols])
    conn.commit()
    conn.close()
    return get_user(chat_id)


def delete_user(chat_id):
    init_users_db()
    conn = _db()
    conn.execute("DELETE FROM bot_users WHERE chat_id=?", (str(chat_id),))
    conn.commit()
    conn.close()


def list_users():
    init_users_db()
    conn = _db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM bot_users WHERE enabled=1").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def to_session_user(row):
    """Row DB -> dict user ala config.load_users()."""
    return {
        "id": row.get("user_id") or f"tg_{row['chat_id']}",
        "telegram_chat_id": str(row["chat_id"]),
        "broker_mode": (row.get("broker_mode") or "paper").lower(),
        "risk_percent": float(row.get("risk_percent") or 0.01),
        "symbol": row.get("symbol"),
        "mt5_login": row.get("mt5_login"),
        "mt5_password": row.get("mt5_password"),
        "mt5_server": row.get("mt5_server"),
        "mt5_path": row.get("mt5_path"),
        "oanda_api_key": row.get("oanda_api_key") or "",
        "oanda_account_id": row.get("oanda_account_id") or "",
        "oanda_env": row.get("oanda_env") or "practice",
        "oanda_instrument": row.get("oanda_instrument") or "XAU_USD",
        "enabled": True,
        "dynamic": True,
    }


def export_json():
    return json.dumps(list_users(), default=str)
