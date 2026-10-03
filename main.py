"""Entry point: multi-user + registrasi mandiri + loop detektor bar M15 (PRD S3)."""
import os
import threading
import time
from datetime import datetime

import pandas as pd

from config import (
    ADMIN_CHAT_IDS,
    BROKER_MODE,
    DAILY_MAX_LOSS_PCT,
    LOCAL_TZ,
    MAX_OPEN_POSITIONS,
    OPEN_REGISTRATION,
    SIGNAL_COOLDOWN_MINUTES,
    TELEGRAM_CHAT_ID,
    load_users,
    user_symbol,
    validate_config,
)
from news_guard import fetch_high_impact_usd_events, is_in_news_blackout
from strategy import add_indicators, evaluate_mtf_strategy, evaluate_session_strategy
from strategy_router import route as route_strategy
import risk_governor
from chart_engine import generate_signal_chart
from telegram_bot import (
    _lock as _pending_lock,
    add_authorized_chat,
    pending_trades,
    register_trade,
    remove_authorized_chat,
    send_photo_to,
    send_text_to,
    set_authorized_chats,
    trade_expiry_cleaner,
    telegram_button_listener,
)
from broker_base import create_broker_for_user
from order_manager import generic_position_lifecycle_manager
from db_logger import daily_reporter_generic, init_db
import user_store

SESSIONS: dict[str, dict] = {}
SESS_LOCK = threading.Lock()
REG: dict[str, dict] = {}  # chat_id -> {step, data, ts}
REG_TTL = 600
LAST_SIGNAL: dict[str, datetime] = {}  # user_id -> waktu sinyal terakhir (cooldown)


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


def start_health_server():
    """HTTP mini agar Render Web Service free lolos health check. Aktif bila PORT di-set."""
    port = int(os.getenv("PORT", "0") or 0)
    if not port:
        return None
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[health] :{port}")
    return srv


def spawn_session(user):
    """Buat broker + thread lifecycle/reporter untuk 1 user. Return True bila aktif."""
    from broker_base import create_broker_for_user as _mk

    try:
        broker = _mk(user)
    except Exception as e:
        print(f"[ERROR] broker {user['id']}: {e}")
        return False
    if not broker.initialize():
        print(f"[ERROR] Gagal inisialisasi broker [{user['id']}:{broker.name}].")
        return False
    stop = threading.Event()
    sym = user_symbol(user)
    chat, uid = str(user["telegram_chat_id"]), user["id"]
    sess = {"user": user, "broker": broker, "symbol": sym, "stop": stop}
    with SESS_LOCK:
        SESSIONS[uid] = sess
    add_authorized_chat(chat)
    threading.Thread(target=generic_position_lifecycle_manager,
                     args=(broker, lambda m, c=chat: send_text_to(c, m), sym, stop),
                     daemon=True).start()
    threading.Thread(target=daily_reporter_generic,
                     args=(broker, lambda m, c=chat: send_text_to(c, m),
                           lambda img, cap, c=chat: send_photo_raw_to(c, img, cap),
                           uid, stop),
                     daemon=True).start()
    print(f"[SESSION] {uid} aktif [{broker.name}:{sym}].")
    return True


def remove_session(user_id):
    """Hentikan thread user + buang pending-nya. Return True bila ada."""
    with SESS_LOCK:
        sess = SESSIONS.pop(user_id, None)
    if not sess:
        return False
    try:
        sess["stop"].set()
    except Exception:
        pass
    chat = str(sess["user"].get("telegram_chat_id"))
    if sess["user"].get("dynamic"):
        remove_authorized_chat(chat)
    with _pending_lock:
        for tid in [t for t, tr in pending_trades.items()
                    if str(tr.get("user_id")) == str(user_id)]:
            pending_trades.pop(tid, None)
    print(f"[SESSION] {user_id} dihentikan.")
    return True


def live_sessions():
    with SESS_LOCK:
        return [s for s in SESSIONS.values() if not s["stop"].is_set()]


def _fetch_mtf(broker, symbol):
    def _get(tf, cnt):
        try:
            if hasattr(broker, "get_rates"):
                return broker.get_rates(symbol, tf, cnt)
        except Exception as e:
            print(f"[WARN] get_rates {tf}: {e}")
        try:
            return broker.get_rates_m15(symbol, cnt) if tf == "M15" else None
        except Exception:
            return None
    return {
        "D1": _get("D1", 80),
        "H4": _get("H4", 80),
        "H1": _get("H1", 250),
        "M15": _get("M15", 100),
        "M5": _get("M5", 80),
    }


def session_by_chat(chat_id):
    with SESS_LOCK:
        for s in SESSIONS.values():
            if str(s["user"].get("telegram_chat_id")) == str(chat_id) \
                    and not s["stop"].is_set():
                return s
    return None


def broadcast_signal(sessions, signal, entry, sl, tp, reason, df,
                       strategy="session_sweep", regime="RANGING", risk_mult=1.0):
    """Kirim 1 sinyal ke semua user; lot dihitung per-broker/risk masing-masing."""
    rr = round(abs(tp - entry) / abs(entry - sl), 1) if abs(entry - sl) > 0 else 0.0
    base = int(time.time())
    ref_symbol = sessions[0]["symbol"] if sessions else "XAUUSD"
    img = f"signal_{base}.png"
    title_suffix = f"[{regime}: {strategy.upper()} {signal}]"
    try:
        generate_signal_chart(df, entry, sl, tp, ref_symbol, filename=img,
                              title_suffix=title_suffix)
    except TypeError:
        # chart_engine lama tanpa title_suffix
        try:
            generate_signal_chart(df, entry, sl, tp, ref_symbol, filename=img)
        except Exception as e:
            print(f"[ERROR] chart: {e}")
            return
    except Exception as e:
        print(f"[ERROR] chart: {e}")
        return
    try:
        for i, s in enumerate(sessions):
            user, broker = s["user"], s["broker"]
            try:
                lot = broker.calculate_lot(entry, sl)
                if risk_mult and risk_mult != 1.0:
                    lot = max(0.01, round(lot * float(risk_mult), 2))
            except Exception:
                lot = 0.01
            base_risk = float(user.get("risk_percent", 0.01) or 0.01) * 100
            risk_pct = base_risk * (float(risk_mult) if risk_mult else 1.0)
            trade_id = f"{user['id']}:tr_{base}_{i}"
            paper_note = "\n🧪 *PAPER — uang virtual, tanpa eksekusi real*" if broker.name == "paper" else ""
            caption = (
                f"🎯 *SINYAL {s['symbol']} {signal} [{broker.name}]*{paper_note}\n━━━━\n"
                f"🧭 Rezim `{regime}` | Strategi `{strategy}`\n"
                f"📌 {reason}\n"
                f"💵 Entry `{entry:.2f}` | 🛑 SL `{sl:.2f}` | 🎯 TP `{tp:.2f}` (1:{rr})\n"
                f"⚖️ Lot `{lot}` (risiko {risk_pct:g}%)\n━━━━\n"
                f"Konfirmasi dalam 5 menit:")
            msg_id = send_photo_to(user["telegram_chat_id"], img, caption, trade_id)
            if msg_id:
                register_trade(trade_id, {
                    "user_id": user["id"],
                    "strategy": strategy, "regime": regime,
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


# ================= ONBOARDING /start =================
def _reg_get(chat_id):
    st = REG.get(str(chat_id))
    if not st:
        return None
    if time.time() - st.get("ts", 0) > REG_TTL:
        REG.pop(str(chat_id), None)
        return None
    return st


def start_onboarding(chat_id):
    if not OPEN_REGISTRATION:
        return ("🔒 Pendaftaran tertutup. Minta admin menambahkan chat ID ini:\n"
                f"`{chat_id}`")
    REG[str(chat_id)] = {"step": "mode", "data": {}, "ts": time.time()}
    return ("👋 *Selamat datang di XAUUSD Bot!*\n━━━━\n"
            "Sinyal akan dikirim ke akun *milikmu sendiri*.\n"
            "Pilih broker (balas angka):\n"
            "1️⃣ OANDA — API key (demo practice / live)\n"
            "2️⃣ Paper — simulasi virtual, tanpa kredensial\n"
            "3️⃣ MT5 — login akun (terbatas 1 terminal)")


def handle_text(chat_id, text):
    """State machine onboarding. Return reply (balasan teks biasa diabaikan)."""
    chat_id = str(chat_id)
    if session_by_chat(chat_id):
        return None  # user terdaftar: teks biasa diabaikan
    st = _reg_get(chat_id)
    if not st:
        return None
    t = text.strip()
    step, data = st["step"], st["data"]
    st["ts"] = time.time()

    if step == "mode":
        if t == "1":
            st.update(step="oanda_key", ts=time.time())
            return ("🔑 Kirim *API token* OANDA kamu (practice atau live).\n"
                    "⚠️ Setelah ini hapus pesan token dari chat ya.")
        if t == "2":
            return finish_registration(chat_id, {"broker_mode": "paper"})
        if t == "3":
            st.update(step="mt5_login", ts=time.time())
            return ("🖥️ Mode MT5 (catatan: 1 server = 1 terminal login).\n"
                    "Kirim *nomor login MT5* kamu.")
        return "Balas *1*, *2*, atau *3*."
    if step == "oanda_key":
        if len(t) < 10:
            return "Token terlalu pendek, coba lagi."
        data["oanda_api_key"] = t
        env, accounts = _detect_oanda(t)
        if not accounts:
            return ("❌ Token tidak valid / tak ada akun (cek practice vs live).\n"
                    "Kirim ulang token yang benar, atau /start untuk ulang.")
        data["oanda_env"] = env
        data["accounts"] = accounts
        if len(accounts) == 1:
            data["oanda_account_id"] = accounts[0]
            return finish_registration(chat_id, {
                "broker_mode": "oanda", "oanda_api_key": t,
                "oanda_env": env, "oanda_account_id": accounts[0]})
        st.update(step="oanda_account", ts=time.time())
        opts = "\n".join(f"{i+1}. `{a}`" for i, a in enumerate(accounts))
        return (f"Token OK ({env}). Pilih akun (balas angka/ID):\n{opts}")
    if step == "oanda_account":
        pick = None
        if t.isdigit() and 1 <= int(t) <= len(data.get("accounts", [])):
            pick = data["accounts"][int(t) - 1]
        elif t in data.get("accounts", []):
            pick = t
        if not pick:
            return "Pilihan tak dikenal, balas angka/ID dari daftar."
        return finish_registration(chat_id, {
            "broker_mode": "oanda", "oanda_api_key": data["oanda_api_key"],
            "oanda_env": data.get("oanda_env", "practice"), "oanda_account_id": pick})
    if step == "mt5_login":
        if not t.isdigit():
            return "Login harus angka, coba lagi."
        data["mt5_login"] = int(t)
        st.update(step="mt5_password", ts=time.time())
        return "Kirim *password* MT5."
    if step == "mt5_password":
        data["mt5_password"] = t
        st.update(step="mt5_server", ts=time.time())
        return ("Kirim *nama server* broker (tepat seperti di terminal, cth. `Broker-Demo`).\n"
                "⚠️ Setelah ini hapus pesan password dari chat ya.")
    if step == "mt5_server":
        data["mt5_server"] = t
        return finish_registration(chat_id, {
            "broker_mode": "mt5", "mt5_login": data.get("mt5_login"),
            "mt5_password": data.get("mt5_password"), "mt5_server": t})
    REG.pop(chat_id, None)
    return "Sesi habis, /start untuk ulang."


def _detect_oanda(token):
    """Coba practice lalu live. Return (env, [account_ids])."""
    from broker_oanda import OandaAdapter

    for env in ("practice", "live"):
        try:
            accs = OandaAdapter(api_key=token, account_id="x", env=env).list_accounts()
        except Exception:
            accs = []
        if accs:
            return env, accs
    return None, []


def finish_registration(chat_id, creds):
    chat_id = str(chat_id)
    row = user_store.save_user(chat_id, user_id=f"tg_{chat_id}", enabled=1, **creds)
    user = user_store.to_session_user(row)
    REG.pop(chat_id, None)
    if spawn_session(user):
        mode = user["broker_mode"]
        extra = ""
        if mode == "oanda":
            extra = f"Akun `{user['oanda_account_id']}` ({user['oanda_env']})."
        elif mode == "paper":
            extra = "Uang virtual, tanpa eksekusi real."
        return (f"✅ Terhubung! Mode `{mode}`. {extra}\n"
                f"Kamu akan terima sinyal di chat ini.\n"
                f"/status /test /unlink — /help untuk daftar.")
    user_store.save_user(chat_id, enabled=0)
    return ("❌ Gagal konek broker (cek kredensial). /start untuk coba lagi.")


def main():
    start_health_server()
    static_users = load_users()
    if len(static_users) == 1 and static_users[0]["id"] == "default" \
            and not os.getenv("USERS_JSON", "").strip():
        missing = validate_config()
        if missing:
            print(f"[ERROR] .env kurang: {', '.join(missing)}. Bot batal jalan.")
            print(f"[INFO] BROKER_MODE={BROKER_MODE}. Lihat .env.example / users.json.example.")
            return
    problems = _validate_users(static_users)
    if problems:
        print(f"[ERROR] Konfig user kurang: {'; '.join(problems)}.")
        return
    set_authorized_chats([u["telegram_chat_id"] for u in static_users])
    init_db()
    user_store.init_users_db()

    for u in static_users:
        u["static"] = True
        spawn_session(u)
    for row in user_store.list_users():
        if any(str(x["telegram_chat_id"]) == str(row["chat_id"]) for x in static_users):
            continue  # statis menang bila chat ganda
        spawn_session(user_store.to_session_user(row))
    sessions = live_sessions()
    if not sessions:
        print("[ERROR] Tidak ada user/broker aktif.")
        return

    def on_execute(trade):
        with SESS_LOCK:
            sess = SESSIONS.get(trade.get("user_id"))
        if sess is None or sess["stop"].is_set():
            return False, "User/broker tidak aktif"
        # Governor race-guard: tombol diklik saat posisi sudah terbuka.
        try:
            poss = sess["broker"].list_positions(sess.get("symbol"))
            if poss and len(poss) >= int(MAX_OPEN_POSITIONS or 1):
                return False, f"Ditolak governor: sudah ada #{poss[0].ticket}"
        except Exception:
            pass
        try:
            return sess["broker"].market_order(
                trade.get("symbol"), trade.get("action"),
                float(trade.get("lot", 0.01)),
                float(trade.get("sl")), float(trade.get("tp")))
        except Exception as e:
            return False, f"Eksekusi gagal: {e}"

    def on_command(chat_id, cmd):
        s = session_by_chat(chat_id)
        if cmd == "/start":
            if s is not None:
                return (f"👋 Akunmu sudah terhubung (`{s['user']['broker_mode']}`).\n"
                        f"/status /test /unlink — /help untuk daftar.")
            return start_onboarding(chat_id)
        if s is None:
            return "⛔ Unauthorized. /start untuk daftar."
        user, broker, sym = s["user"], s["broker"], s["symbol"]
        if cmd in ("/help",):
            return (
                f"🤖 *XAUUSD Bot [{broker.name}]*\n━━━━\n"
                f"Mode `{user['broker_mode']}` | Symbol `{sym}`\n"
                f"Window 14:00-23:00 WIB, approve 5 mnt.\n━━━━\n"
                f"/status — saldo, posisi, sinyal pending\n"
                f"/test — contoh sinyal + tombol (tanpa eksekusi)\n"
                f"/unlink — putus akun dari chat ini" +
                ("\n/users — daftar user (admin)" if str(chat_id) in ADMIN_CHAT_IDS else ""))
        if cmd == "/status":
            try:
                bal = broker.get_balance()
            except Exception:
                bal = 0.0
            try:
                poss = broker.list_positions(sym)
            except Exception:
                poss = []
            with _pending_lock:
                pend = sum(1 for t in pending_trades.values()
                           if str(t.get("chat_id")) == str(chat_id))
            lines = [
                f"📊 *STATUS [{user['id']}:{broker.name}]*",
                f"🏦 Saldo: `${bal:,.2f}`",
                f"📌 Posisi terbuka: `{len(poss)}`",
            ]
            for p in poss[:5]:
                lines.append(f"• #{p.ticket} {p.side} {p.volume_lot} @ `{p.price_open:.2f}` SL `{p.sl:.2f}`")
            lines.append(f"⏳ Sinyal pending: `{pend}`")
            return "\n".join(lines)
        if cmd == "/test":
            feed = live_sessions()
            if not feed:
                return "Tidak ada sesi aktif."
            feed = feed[0]
            df = feed["broker"].get_rates_m15(feed["symbol"], 100)
            if df is None or len(df) < 60:
                return "Feed belum siap, coba lagi sebentar."
            df = add_indicators(df)
            entry = float(df.iloc[-2]["close"])
            sl, tp = entry - 5.0, entry + 10.0
            img = f"test_{int(time.time())}.png"
            try:
                generate_signal_chart(df, entry, sl, tp, sym, filename=img)
            except Exception as e:
                return f"Gagal buat chart: {e}"
            trade_id = f"{user['id']}:test_{int(time.time())}"
            caption = (f"🧪 *TEST {sym} BUY [{broker.name}]*\n━━━━\n"
                       f"Contoh tampilan sinyal. Approve = TEST OK, tanpa eksekusi.")
            msg_id = send_photo_to(chat_id, img, caption, trade_id)
            if os.path.exists(img):
                os.remove(img)
            if not msg_id:
                return "Gagal kirim chart test."
            register_trade(trade_id, {
                "user_id": user["id"], "test": True,
                "symbol": sym, "action": "BUY", "entry": entry,
                "sl": sl, "tp": tp, "lot": 0.01,
                "created_at": datetime.now(), "chat_id": chat_id,
                "message_id": msg_id, "caption": caption})
            return None
        if cmd == "/unlink":
            if not user.get("dynamic"):
                return "Akun statis (config server) tak bisa unlink. Minta admin."
            remove_session(user["id"])
            user_store.delete_user(chat_id)
            return "🔌 Akun diputus dari chat ini. /start untuk daftar lagi."
        if cmd == "/users":
            if str(chat_id) not in ADMIN_CHAT_IDS:
                return "⛔ Khusus admin."
            with SESS_LOCK:
                ids = [f"{v['user']['id']}:{v['broker'].name}" for v in SESSIONS.values()]
            return f"👥 Sesi aktif `{len(ids)}`:\n" + ("\n".join(f"• `{i}`" for i in ids) or "-")
        return "Perintah tak dikenal. /help"

    def on_text(chat_id, text):
        return handle_text(chat_id, text)

    threading.Thread(target=telegram_button_listener,
                     args=(on_execute, on_command, on_text), daemon=True).start()
    threading.Thread(target=trade_expiry_cleaner, daemon=True).start()
    print(f"🔥 Bot aktif ({len(live_sessions())} user): listener + expiry + lifecycle + reporter.")
    if not hasattr(main, "_last_fetch"):
        main._last_fetch = datetime.now(LOCAL_TZ)

    while True:
        now = datetime.now(LOCAL_TZ)
        six_am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= six_am and main._last_fetch < six_am:  # PRD 4.1: refresh 06:00 WIB.
            events_cache["events"] = fetch_high_impact_usd_events()
            main._last_fetch = now
        sessions = live_sessions()
        if not sessions:
            time.sleep(10)
            continue
        feed = sessions[0]
        mtf = _fetch_mtf(feed["broker"], feed["symbol"])
        df = mtf.get("M15")
        if df is None or len(df) < 60:
            time.sleep(5)
            continue
        if df.iloc[-2]["time"] == getattr(main, "_last_bar", None):
            time.sleep(10)
            continue
        main._last_bar = df.iloc[-2]["time"]
        print(f"\n[{now:%H:%M:%S}] Bar M15 tutup, evaluasi rezim...")
        df = add_indicators(df)
        if "EMA21" not in df.columns:
            df["EMA21"] = df["close"].ewm(span=21, adjust=False).mean()
        blocked, title = is_in_news_blackout(events_cache["events"])
        if blocked:
            print(f"⛔ Blackout: {title}.")
            time.sleep(10)
            continue
        try:
            res = route_strategy(
                mtf.get("H1"), df, events_cache.get("events"), now)
            if len(res) == 8:
                signal, entry, sl, tp, reason, regime, strategy, risk_mult = res
            else:  # kompat router lama 7-tuple
                signal, entry, sl, tp, reason, regime, strategy = res
                risk_mult = 1.0
            label = {"TRENDING": "Trend Pullback/Breakout",
                     "POST_NEWS": "Post-News",
                     "RANGING": "Session Sweep"}.get(regime, regime)
            print(f"[REGIME] {reason.split(':')[0]}. Memeriksa {label}...")
            # Kompat MTF lama: bila H1 kurang dari 60 bar, router fallback RANGING;
            # biarkan sweep yang menentukan. Tidak ada fallback MTF 3-lapis di sini
            # karena PRD V2 memakai router sebagai dispatcher utama.
        except Exception as e:
            print(f"[ERROR] router: {e}")
            signal, entry, sl, tp, reason = evaluate_session_strategy(df)
            regime, strategy, risk_mult = "RANGING", "session_sweep", 1.0
        if signal:
            eligible, skipped = [], []
            for s in sessions:
                ok, why = risk_governor.eligible(
                    s, LAST_SIGNAL.get(s["user"]["id"]), now,
                    cooldown_min=SIGNAL_COOLDOWN_MINUTES,
                    max_loss_pct=DAILY_MAX_LOSS_PCT)
                (eligible if ok else skipped).append((s, why))
            for _, why in skipped:
                print(f"[SKIP] {why}")
            if not eligible:
                print(f"[SKIP] {reason} (governor: semua user terblokir)")
            else:
                broadcast_signal([s for s, _ in eligible], signal, entry, sl,
                                 tp, reason, df, strategy=strategy,
                                 regime=regime, risk_mult=risk_mult)
                for s, _ in eligible:
                    LAST_SIGNAL[s["user"]["id"]] = now
        else:
            print(f"[SKIP] {reason}")
        time.sleep(10)


events_cache = {"events": []}


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
    events_cache["events"] = fetch_high_impact_usd_events()
    _last_fetch = datetime.now(LOCAL_TZ)
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
