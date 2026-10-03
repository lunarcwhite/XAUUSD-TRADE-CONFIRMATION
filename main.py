"""Entry point: orkestrasi multi-user + loop detektor bar M15 (PRD S3)."""
import os
import threading
import time
from datetime import datetime

import pandas as pd

from config import (
    BROKER_MODE,
    LOCAL_TZ,
    TELEGRAM_CHAT_ID,
    load_users,
    user_symbol,
    validate_config,
)
from news_guard import fetch_high_impact_usd_events, is_in_news_blackout
from strategy import add_indicators, evaluate_session_strategy
from chart_engine import generate_signal_chart
from telegram_bot import (
    register_trade,
    send_photo_to,
    send_text_to,
    set_authorized_chats,
    trade_expiry_cleaner,
    telegram_button_listener,
)
from broker_base import create_broker_for_user
from order_manager import generic_position_lifecycle_manager
from db_logger import daily_reporter_generic, init_db


def _validate_users(users):
    problems = []
    for u in users:
        if not u.get("telegram_chat_id"):
            problems.append(f"{u.get('id')}: TELEGRAM_CHAT_ID kosong")
        if (u.get("broker_mode") or "mt5") == "oanda":
            if not (u.get("oanda_api_key") or "").strip() or str(u.get("oanda_api_key")).startswith("isi_"):
                problems.append(f"{u.get('id')}: OANDA_API_KEY kosong")
            if not (u.get("oanda_account_id") or "").strip() or str(u.get("oanda_account_id")).startswith("isi_"):
                problems.append(f"{u.get('id')}: OANDA_ACCOUNT_ID kosong")
    return problems


def broadcast_signal(sessions, signal, entry, sl, tp, reason, df):
    """Kirim 1 sinyal ke semua user; lot dihitung per-broker/risk masing-masing."""
    rr = round(abs(tp - entry) / abs(entry - sl), 1) if abs(entry - sl) > 0 else 0.0
    base = int(time.time())
    # Chart generik sekali (pakai symbol sesi pertama untuk label).
    ref_symbol = sessions[0]["symbol"] if sessions else "XAUUSD"
    img = f"signal_{base}.png"
    try:
        generate_signal_chart(df, entry, sl, tp, ref_symbol, filename=img)
    except Exception as e:
        print(f"[ERROR] chart: {e}")
        return
    try:
        for i, s in enumerate(sessions):
            user, broker = s["user"], s["broker"]
            try:
                lot = broker.calculate_lot(entry, sl)
            except Exception:
                lot = 0.01
            risk_pct = float(user.get("risk_percent", 0.01) or 0.01) * 100
            trade_id = f"{user['id']}:tr_{base}_{i}"
            caption = (
                f"🎯 *SINYAL {s['symbol']} {signal} [{broker.name}]*\n━━━━\n"
                f"📌 {reason}\n"
                f"💵 Entry `{entry:.2f}` | 🛑 SL `{sl:.2f}` | 🎯 TP `{tp:.2f}` (1:{rr})\n"
                f"⚖️ Lot `{lot}` (risiko {risk_pct:g}%)\n━━━━\n"
                f"Konfirmasi dalam 5 menit:")
            msg_id = send_photo_to(user["telegram_chat_id"], img, caption, trade_id)
            if msg_id:
                register_trade(trade_id, {
                    "user_id": user["id"],
                    "symbol": s["symbol"], "action": signal, "entry": entry,
                    "sl": sl, "tp": tp, "lot": lot,
                    "created_at": datetime.now(), "chat_id": user["telegram_chat_id"],
                    "message_id": msg_id, "caption": caption})
                print(f"[SINYAL] {signal} {trade_id} -> {user['id']}.")
            else:
                print(f"[ERROR] Gagal kirim sinyal ke {user['id']}.")
    finally:
        if os.path.exists(img):
            os.remove(img)


def main():
    users = load_users()
    # Fallback validasi single-user lama bila hanya default.
    if len(users) == 1 and users[0]["id"] == "default" and not os.getenv("USERS_JSON", "").strip():
        missing = validate_config()
        if missing:
            print(f"[ERROR] .env kurang: {', '.join(missing)}. Bot batal jalan.")
            print(f"[INFO] BROKER_MODE={BROKER_MODE}. Lihat .env.example / users.json.example.")
            return
    problems = _validate_users(users)
    if problems:
        print(f"[ERROR] Konfig user kurang: {'; '.join(problems)}.")
        return
    set_authorized_chats([u["telegram_chat_id"] for u in users])
    init_db()

    sessions = []
    for u in users:
        try:
            broker = create_broker_for_user(u)
        except Exception as e:
            print(f"[ERROR] broker {u['id']}: {e}")
            continue
        if not broker.initialize():
            print(f"[ERROR] Gagal inisialisasi broker [{u['id']}:{broker.name}]. User dinonaktifkan.")
            continue
        sessions.append({"user": u, "broker": broker, "symbol": user_symbol(u)})
    if not sessions:
        print("[ERROR] Tidak ada user/broker aktif.")
        return

    brokers_by_user = {s["user"]["id"]: s["broker"] for s in sessions}

    def on_execute(trade):
        b = brokers_by_user.get(trade.get("user_id"))
        if b is None:
            return False, "User/broker tidak dikenal"
        try:
            return b.market_order(
                trade.get("symbol"), trade.get("action"),
                float(trade.get("lot", 0.01)),
                float(trade.get("sl")), float(trade.get("tp")))
        except Exception as e:
            return False, f"Eksekusi gagal: {e}"

    threading.Thread(target=telegram_button_listener, args=(on_execute,), daemon=True).start()
    threading.Thread(target=trade_expiry_cleaner, daemon=True).start()
    for s in sessions:
        u, b, sym = s["user"], s["broker"], s["symbol"]
        chat = u["telegram_chat_id"]
        uid = u["id"]
        threading.Thread(target=generic_position_lifecycle_manager,
                         args=(b, lambda m, c=chat: send_text_to(c, m), sym),
                         daemon=True).start()
        threading.Thread(target=daily_reporter_generic,
                         args=(b, lambda m, c=chat: send_text_to(c, m),
                               lambda img, cap, c=chat: send_photo_raw_to(c, img, cap),
                               uid),
                         daemon=True).start()
    print(f"🔥 Bot aktif multi-user ({len(sessions)} user): listener + expiry + lifecycle + reporter.")

    # Data M15 dari broker sesi pertama (harga antar-broker ≈ sama untuk sinyal).
    feed = sessions[0]
    events = fetch_high_impact_usd_events()
    last_fetch = datetime.now(LOCAL_TZ)
    last_bar = None

    while True:
        now = datetime.now(LOCAL_TZ)
        six_am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= six_am and last_fetch < six_am:  # PRD 4.1: refresh harian 06:00 WIB.
            events = fetch_high_impact_usd_events()
            last_fetch = now

        df = feed["broker"].get_rates_m15(feed["symbol"], 100)
        if df is None or len(df) < 60:
            time.sleep(5)
            continue
        if df.iloc[-2]["time"] == last_bar:
            time.sleep(10)
            continue
        last_bar = df.iloc[-2]["time"]
        print(f"\n[{now:%H:%M:%S}] Bar M15 tutup, evaluasi...")

        df = add_indicators(df)
        blocked, title = is_in_news_blackout(events)
        if blocked:
            print(f"⛔ Blackout: {title}.")
            time.sleep(10)
            continue

        signal, entry, sl, tp, reason = evaluate_session_strategy(df)
        if signal:
            broadcast_signal(sessions, signal, entry, sl, tp, reason, df)
        else:
            print(f"[SKIP] {reason}")
        time.sleep(10)


def send_photo_raw_to(chat_id, image_path, caption):
    """Adaptor reporter per-user → sendPhoto tanpa tombol."""
    from telegram_bot import _API
    import requests
    try:
        with open(image_path, "rb") as photo:
            requests.post(
                f"{_API}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption,
                      "parse_mode": "Markdown"},
                files={"photo": photo}, timeout=15)
    except Exception as e:
        print(f"[ERROR] kirim kurva: {e}")


def send_photo_with_buttons_raw(image_path, caption):
    """Kompat lama single-user."""
    from config import TELEGRAM_CHAT_ID as _CHAT

    send_photo_raw_to(_CHAT, image_path, caption)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot berhenti.")
    finally:
        try:
            import MetaTrader5 as mt5

            mt5.shutdown()
        except Exception:
            pass
