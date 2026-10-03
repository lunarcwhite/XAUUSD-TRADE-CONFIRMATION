"""Notifikasi sinyal + tombol persetujuan + expiry 5 menit (PRD S4.4)."""
import json
import threading
import time
from datetime import datetime

import requests

from config import EXPIRY_SECONDS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

pending_trades = {}
_lock = threading.Lock()
_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Multi-user: chat yang boleh eksekusi. Default single-user lama.
_AUTHORIZED_CHATS: set[str] = {str(TELEGRAM_CHAT_ID)} if TELEGRAM_CHAT_ID else set()


def set_authorized_chats(chats):
    global _AUTHORIZED_CHATS
    _AUTHORIZED_CHATS = {str(c) for c in chats if str(c)}


def add_authorized_chat(chat_id):
    _AUTHORIZED_CHATS.add(str(chat_id))


def remove_authorized_chat(chat_id):
    _AUTHORIZED_CHATS.discard(str(chat_id))


def send_text_to(chat_id, message):
    """Kirim teks ke chat tertentu (multi-user)."""
    try:
        r = requests.post(
            f"{_API}/sendMessage",
            json={"chat_id": chat_id, "text": message,
                  "parse_mode": "Markdown"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[ERROR] send_text: {e}")
        return False


def send_text(message):
    """Kompat single-user lama → kirim ke TELEGRAM_CHAT_ID."""
    return send_text_to(TELEGRAM_CHAT_ID, message)


def send_photo_to(chat_id, image_path, caption, trade_id):
    """Kirim chart + tombol ke chat tertentu. Return message_id."""
    keyboard = {"inline_keyboard": [[
        {"text": "🟢 Buka Posisi", "callback_data": f"exec:{trade_id}"},
        {"text": "🔴 Abaikan", "callback_data": f"ignore:{trade_id}"},
    ]]}
    try:
        with open(image_path, "rb") as photo:
            r = requests.post(
                f"{_API}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption,
                      "parse_mode": "Markdown",
                      "reply_markup": json.dumps(keyboard)},
                files={"photo": photo},
                timeout=15,
            )
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"[ERROR] send_photo: {e}")
        return None


def send_photo_with_buttons(image_path, caption, trade_id):
    """Kompat single-user lama. Untuk multi-user pakai send_photo_to()."""
    # trade mungkin sudah terdaftar → kirim ke owner; fallback ke global.
    with _lock:
        trade = pending_trades.get(trade_id)
    chat = (trade or {}).get("chat_id") or TELEGRAM_CHAT_ID
    return send_photo_to(chat, image_path, caption, trade_id)


def answer_callback(callback_query_id, text=""):
    try:
        requests.post(f"{_API}/answerCallbackQuery",
                      json={"callback_query_id": callback_query_id,
                            "text": text},
                      timeout=5)
    except Exception:
        pass


def edit_caption(chat_id, message_id, new_caption):
    """Ganti caption + hapus tombol (anti double-click / expiry)."""
    try:
        requests.post(
            f"{_API}/editMessageCaption",
            json={"chat_id": chat_id, "message_id": message_id,
                  "caption": new_caption, "parse_mode": "Markdown",
                  "reply_markup": json.dumps({"inline_keyboard": []})},
            timeout=10,
        )
    except Exception as e:
        print(f"[ERROR] edit_caption: {e}")


def register_trade(trade_id, trade):
    with _lock:
        pending_trades[trade_id] = trade


def telegram_button_listener(on_execute, on_command=None, on_text=None):
    """Long-poll getUpdates.

    on_execute(trade) -> (ok, detail). Trade test (/test) tidak dieksekusi.
    on_command(chat_id, command) -> reply str | None (command /xxx).
    on_text(chat_id, text) -> reply str | None (pesan biasa, cth. onboarding).
    Guard user + 300 dtk.
    """
    print("🤖 Telegram listener aktif...")
    offset = 0
    while True:
        try:
            r = requests.get(f"{_API}/getUpdates",
                             params={"offset": offset, "timeout": 20},
                             timeout=25).json()
            for item in r.get("result", []):
                offset = item["update_id"] + 1
                if "message" in item and (on_command is not None or on_text is not None):
                    msg = item["message"]
                    text = (msg.get("text") or "").strip()
                    if not text:
                        continue
                    chat_id = msg["chat"]["id"]
                    if text.startswith("/"):
                        if on_command is None:
                            continue
                        cmd = text.split()[0].split("@")[0].lower()
                        # /start selalu diteruskan (pintu onboarding user baru).
                        if cmd not in ("/start",) and _AUTHORIZED_CHATS \
                                and str(chat_id) not in _AUTHORIZED_CHATS:
                            send_text_to(chat_id, "⛔ Unauthorized.")
                            continue
                        try:
                            reply = on_command(str(chat_id), cmd)
                        except Exception as e:
                            reply = f"Command gagal: {e}"
                        if reply:
                            send_text_to(chat_id, reply)
                    elif on_text is not None:
                        try:
                            reply = on_text(str(chat_id), text)
                        except Exception as e:
                            reply = f"Gagal: {e}"
                        if reply:
                            send_text_to(chat_id, reply)
                    continue
                if "callback_query" not in item:
                    continue
                cb = item["callback_query"]
                action, _, trade_id = cb.get("data", "").partition(":")
                chat_id = cb["message"]["chat"]["id"]
                if _AUTHORIZED_CHATS and str(chat_id) not in _AUTHORIZED_CHATS:
                    answer_callback(cb["id"], "Unauthorized.")
                    continue
                msg_id = cb["message"]["message_id"]
                caption = cb["message"].get("caption", "")

                with _lock:
                    trade = pending_trades.get(trade_id)
                if trade is None:
                    answer_callback(cb["id"], "⚠️ Sinyal kedaluwarsa/diproses.")
                    continue
                # Isolasi antar-user: hanya owner yang boleh klik tombolnya.
                if str(trade.get("chat_id")) != str(chat_id):
                    answer_callback(cb["id"], "Bukan sinyal untuk akun ini.")
                    continue
                if (datetime.now() - trade["created_at"]).total_seconds() > EXPIRY_SECONDS:
                    answer_callback(cb["id"], "⚠️ Waktu konfirmasi habis.")
                    edit_caption(chat_id, msg_id,
                                 f"{caption}\n\n⏰ *STATUS: KEDALUWARSA*")
                    with _lock:
                        pending_trades.pop(trade_id, None)
                    continue

                if action == "exec":
                    if trade.get("test"):
                        answer_callback(cb["id"], "Test OK.")
                        edit_caption(chat_id, msg_id,
                                     f"{caption}\n\n━━━━\n✅ *STATUS: TEST OK (tidak dieksekusi)*")
                        with _lock:
                            pending_trades.pop(trade_id, None)
                        continue
                    answer_callback(cb["id"], "⏳ Eksekusi ke broker...")
                    ok, detail = on_execute(trade)
                    status = ("✅ *STATUS: DIEKSEKUSI*" if ok
                              else "❌ *STATUS: EKSEKUSI GAGAL*")
                    edit_caption(chat_id, msg_id,
                                 f"{caption}\n\n━━━━\n{status}\n📝 {detail}")
                    with _lock:
                        pending_trades.pop(trade_id, None)
                elif action == "ignore":
                    answer_callback(cb["id"], "Sinyal diabaikan.")
                    edit_caption(chat_id, msg_id,
                                 f"{caption}\n\n🚫 *STATUS: DIABAIKAN*")
                    with _lock:
                        pending_trades.pop(trade_id, None)
        except Exception:
            time.sleep(2)


def trade_expiry_cleaner():
    """Hapus tombol sinyal yang tak diklik dalam 5 menit."""
    print("⏳ Expiry cleaner aktif (5 menit)...")
    while True:
        try:
            now = datetime.now()
            with _lock:
                expired = [tid for tid, t in pending_trades.items()
                           if (now - t["created_at"]).total_seconds() > EXPIRY_SECONDS]
            for tid in expired:
                with _lock:
                    trade = pending_trades.pop(tid, None)
                if trade and trade.get("message_id"):
                    edit_caption(trade["chat_id"], trade["message_id"],
                                 f"{trade['caption']}\n\n⏰ *STATUS: KEDALUWARSA*")
                print(f"[EXPIRED] {tid}")
        except Exception as e:
            print(f"[ERROR] cleaner: {e}")
        time.sleep(5)
