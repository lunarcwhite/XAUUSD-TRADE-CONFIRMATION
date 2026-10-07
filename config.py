"""Konstanta trading & kredensial Telegram (PRD S3: config.py)."""
import os
from zoneinfo import ZoneInfo


def _load_dotenv():
    """Muat KEY=VALUE dari .env sebelah file ini (stdlib, tanpa dependensi)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    except OSError:
        pass


_load_dotenv()

# ================= KREDENSIAL TELEGRAM =================
# Wajib via .env (lihat .env.example). Tanpa default asli agar tak bocor ke git.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ================= KREDENSIAL MT5 (opsional) =================
# Jika diisi, main.py login via mt5.initialize() tanpa terminal terbuka.
# MT5_LOGIN berupa integer; biarkan kosong untuk pakai terminal yang login.
_MT5_LOGIN_RAW = os.getenv("MT5_LOGIN", "").strip()
try:
    MT5_LOGIN = int(_MT5_LOGIN_RAW) if _MT5_LOGIN_RAW else None
except ValueError:
    MT5_LOGIN = None
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "") or None
MT5_SERVER = os.getenv("MT5_SERVER", "") or None
MT5_PATH = os.getenv("MT5_PATH", "") or None


def _is_placeholder(v):
    v = (v or "").strip()
    return v == "" or v.startswith("isi_") or v in {"password_akun", "12345678"}


def validate_config():
    """Return daftar kredensial wajib yang masih kosong/placeholder. [] = siap jalan."""
    missing = []
    if _is_placeholder(TELEGRAM_BOT_TOKEN):
        missing.append("TELEGRAM_BOT_TOKEN")
    if _is_placeholder(TELEGRAM_CHAT_ID):
        missing.append("TELEGRAM_CHAT_ID")
    if BROKER_MODE == "paper":
        return missing  # paper hanya butuh Telegram
    if BROKER_MODE == "ctrader":
        if _is_placeholder(CTRADER_CLIENT_ID):
            missing.append("CTRADER_CLIENT_ID")
        if _is_placeholder(CTRADER_CLIENT_SECRET):
            missing.append("CTRADER_CLIENT_SECRET")
        if _is_placeholder(CTRADER_ACCESS_TOKEN):
            missing.append("CTRADER_ACCESS_TOKEN")
        if _is_placeholder(CTRADER_ACCOUNT_ID):
            missing.append("CTRADER_ACCOUNT_ID")
        if CTRADER_ENV not in ("demo", "live"):
            missing.append("CTRADER_ENV (demo/live)")
        return missing
    if BROKER_MODE == "oanda":
        if _is_placeholder(OANDA_API_KEY):
            missing.append("OANDA_API_KEY")
        if _is_placeholder(OANDA_ACCOUNT_ID):
            missing.append("OANDA_ACCOUNT_ID")
        if OANDA_ENV not in ("practice", "live"):
            missing.append("OANDA_ENV (practice/live)")
        return missing
    if _MT5_LOGIN_RAW and MT5_LOGIN is None:
        missing.append("MT5_LOGIN (harus angka)")
    if any(v is not None for v in (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)):
        if MT5_LOGIN is None:
            missing.append("MT5_LOGIN")
        if not MT5_PASSWORD:
            missing.append("MT5_PASSWORD")
        if not MT5_SERVER:
            missing.append("MT5_SERVER")
    return missing

# ================= MODE BROKER =================
# "mt5" = terminal desktop Windows (default, kompatibel lama).
# "oanda" = REST API key (practice/demo atau live, tanpa terminal, cross-OS).
# "paper" = simulasi tanpa broker (feed Yahoo GC=F, uang virtual, untuk tes win rate).
BROKER_MODE = os.getenv("BROKER_MODE", "mt5").strip().lower() or "mt5"

# ================= PAPER (simulasi, tanpa kredensial) =================
try:
    PAPER_BALANCE = float(os.getenv("PAPER_BALANCE", "10000") or 10000)
except ValueError:
    PAPER_BALANCE = 10000.0
PAPER_YAHOO_SYMBOL = os.getenv("PAPER_YAHOO_SYMBOL", "GC=F").strip() or "GC=F"

# ================= KREDENSIAL OANDA v20 =================
# Demo/testing: daftar practice di OANDA, dapat API token + Account ID.
# ENV: practice -> api-fxpractice.oanda.com, live -> api-fxtrade.oanda.com
OANDA_API_KEY = os.getenv("OANDA_API_KEY", "").strip()
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "").strip()
OANDA_ENV = os.getenv("OANDA_ENV", "practice").strip().lower() or "practice"
OANDA_INSTRUMENT = (os.getenv("OANDA_INSTRUMENT") or os.getenv("OANDA_INSTRUMENT_X") or "XAU_USD").strip() or "XAU_USD"
# Konversi lot MT5 -> units OANDA untuk XAU: 1.00 lot = 100 oz = 100 units.
OANDA_UNITS_PER_LOT = 100.0

# ================= KREDENSIAL cTrader Open API =================
# App dibuat di cTrader ID (clientId/secret milik server, satu untuk semua user).
# Tiap user cukup menyimpan ACCESS TOKEN + ACCOUNT ID (OAuth2, bisa refresh).
# Status: adapter penuh tapi BELUM terverifikasi live (butuh token demo).
CTRADER_CLIENT_ID = os.getenv("CTRADER_CLIENT_ID", "").strip()
CTRADER_CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET", "").strip()
CTRADER_ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN", "").strip()
CTRADER_ACCOUNT_ID = os.getenv("CTRADER_ACCOUNT_ID", "").strip()
CTRADER_ENV = os.getenv("CTRADER_ENV", "demo").strip().lower() or "demo"
CTRADER_SYMBOL = os.getenv("CTRADER_SYMBOL", "XAUUSD").strip().upper() or "XAUUSD"

# ================= TRADING =================
# SYMBOL dipakai untuk mode MT5. Mode OANDA pakai OANDA_INSTRUMENT (default XAU_USD).
SYMBOL = "XAUUSD"
TIMEFRAME = 15  # mt5.TIMEFRAME_M15 (tanpa import mt5 agar ringan diuji)
TIMEFRAME_M5 = 5  # mt5.TIMEFRAME_M5 (kompat regime_classifier lama)
TIMEFRAME_M15 = 15  # mt5.TIMEFRAME_M15 (kompat regime_classifier lama)
TIMEFRAME_H1 = 60  # mt5.TIMEFRAME_H1 (kompat regime_classifier lama)
RISK_PERCENT = 0.01  # 1% modal per sinyal
MAGIC_NUMBER = 1002026
DEVIATION = 30  # slippage maks 30 poin ($0.30)
EXPIRY_SECONDS = 300  # timeout persetujuan Telegram 5 menit (default SNIPER, kompat lama)

# Lifecycle: BE + trailing (PRD S4.5, default SNIPER)
BE_BUFFER = 0.20  # $0.20 di atas/bawah entry
TRAILING_DISTANCE = 2.00  # $2.00 di belakang harga (default SNIPER)
TRAILING_STEP = 0.50  # update SL min $0.50 (anti-spam)

# ================= DUAL-MODE V3.0 (PRD S4) =================
# Mode operasional: SNIPER (M15 selektif) atau INTRADAY (M5 cepat + rem harian).
# Bisa di-override via env BOT_MODE=sniper|intraday.
MODE_SNIPER = "SNIPER"
MODE_INTRADAY = "INTRADAY"
BOT_MODE = os.getenv("BOT_MODE", MODE_SNIPER).strip().upper() or MODE_SNIPER
if BOT_MODE not in (MODE_SNIPER, MODE_INTRADAY):
    BOT_MODE = MODE_SNIPER

# --- Sniper / Precision Mode (M15) ---
SNIPER_TIMEFRAME = "M15"
SNIPER_EXPIRY_SECONDS = 300  # 5 menit (PRD S4: tombol Sniper)
SNIPER_MAX_DRIFT = 1.50  # $1.50 toleransi geser harga
SNIPER_TRAILING_DISTANCE = 2.00  # $2.00 di belakang harga
SNIPER_SL_BUFFER = 1.00  # Swing M15 +/- $1.00
SNIPER_EXEC_START_HOUR = 14
SNIPER_EXEC_END_HOUR = 23  # 14:00-23:00 WIB, overnight diizinkan

# --- Intraday Disciplined Mode (M5) ---
INTRADAY_TIMEFRAME = "M5"
INTRADAY_EXPIRY_SECONDS = 90  # 1.5 menit (PRD S4: tombol Intraday)
INTRADAY_MAX_DRIFT = 0.80  # $0.80 toleransi geser harga (Price Drift Guard)
INTRADAY_TRAILING_DISTANCE = 1.50  # $1.50 di belakang harga
INTRADAY_SL_ATR_MULT = 1.2  # SL = Swing M5 +/- 1.2 * ATR14 M5
INTRADAY_ATR_PERIOD = 14
INTRADAY_MIN_RR = 2.0  # TP minimal 1:2 R:R
VWAP_RESET_HOUR = 6  # Session VWAP reset 06:00 WIB
# Jendela entry Intraday: 14:30-17:00 & 19:30-22:30 WIB (PRD S4).
INTRADAY_SESSIONS = ((14, 30, 17, 0), (19, 30, 22, 30))
INTRADAY_NO_ENTRY_HOUR = 22
INTRADAY_NO_ENTRY_MINUTE = 30  # tolak entri >= 22:30 WIB
INTRADAY_MAX_TRADES_PER_DAY = 3  # kuota 3 trade/hari
INTRADAY_KILL_LOSS_PCT = 0.02  # kill switch rugi harian >= 2% balance
FORCE_FLAT_HOUR = 23  # Auto-Flat Intraday 23:00 WIB


def expiry_for_mode(mode):
    """Batas tombol Telegram per mode: 90 dtk Intraday, 300 dtk Sniper."""
    return (INTRADAY_EXPIRY_SECONDS
            if str(mode or "").upper() == MODE_INTRADAY
            else SNIPER_EXPIRY_SECONDS)


def drift_for_mode(mode):
    """Toleransi Price Drift Guard per mode: $0.80 vs $1.50."""
    return (INTRADAY_MAX_DRIFT
            if str(mode or "").upper() == MODE_INTRADAY
            else SNIPER_MAX_DRIFT)


def trailing_for_mode(mode):
    """Jarak trailing per mode: $1.50 vs $2.00."""
    return (INTRADAY_TRAILING_DISTANCE
            if str(mode or "").upper() == MODE_INTRADAY
            else SNIPER_TRAILING_DISTANCE)

# ================= RISK GOVERNOR =================
# Anti-overtrading untuk router 4-cabang (agregat sinyal lebih sering).
MAX_OPEN_POSITIONS = 1  # maks posisi terbuka per user sebelum sinyal baru diblokir
SIGNAL_COOLDOWN_MINUTES = 60  # jeda minimum antar sinyal per user
POST_NEWS_COOLDOWN_MINUTES = 30  # jeda khusus antar sinyal post_news per user
DAILY_MAX_LOSS_PCT = 0.03  # blokir sinyal baru bila rugi terealisasi hari ini >= 3% balance

# ================= SESI & NEWS =================
LOCAL_TZ = ZoneInfo("Asia/Jakarta")
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_BUFFER_MINUTES = 30  # blackout +-30 mnt
ASIAN_START_HOUR = 6
ASIAN_END_HOUR = 14
EXEC_START_HOUR = 14
EXEC_END_HOUR = 23

# ================= DATABASE =================
DB_NAME = "trading_journal.db"
DB_PATH = DB_NAME  # alias kompat state_manager lama (PRD V3: state_manager pakai DB_PATH)


def is_in_intraday_session(now=None):
    """True bila now di dalam salah satu jendela Intraday (PRD S4)."""
    from datetime import datetime as _dt

    try:
        now = now or _dt.now(LOCAL_TZ)
        hm = (now.hour, now.minute)
        for sh, sm, eh, em in INTRADAY_SESSIONS:
            if (sh, sm) <= hm < (eh, em):
                return True
        return False
    except Exception:
        return False


def is_intraday_entry_closed(now=None):
    """True bila >= 22:30 WIB (PRD 5.1: tolak entri mendekati Auto-Flat)."""
    from datetime import datetime as _dt

    try:
        now = now or _dt.now(LOCAL_TZ)
        return (now.hour > INTRADAY_NO_ENTRY_HOUR
                or (now.hour == INTRADAY_NO_ENTRY_HOUR
                    and now.minute >= INTRADAY_NO_ENTRY_MINUTE))
    except Exception:
        return False


def oanda_base_url(env=None):
    """Base URL OANDA v20. practice = demo/testing, live = real."""
    e = (env or OANDA_ENV or "practice").lower()
    if e == "live":
        return "https://api-fxtrade.oanda.com/v3"
    return "https://api-fxpractice.oanda.com/v3"


def active_symbol():
    """Symbol/instrument sesuai BROKER_MODE (jalur single-user lama)."""
    if BROKER_MODE == "oanda":
        return OANDA_INSTRUMENT
    return SYMBOL


# ================= REGISTRASI MANDIRI =================
# true = siapa pun yang /start bisa daftar pakai akun broker sendiri.
# false = hanya chat di USERS_JSON/users.json (minta ke admin).
OPEN_REGISTRATION = os.getenv("OPEN_REGISTRATION", "true").strip().lower() not in {"0", "false", "no"}
# Admin = chat pertama (kompatibel TELEGRAM_CHAT_ID lama) + tambahan opsional.
ADMIN_CHAT_IDS = {str(TELEGRAM_CHAT_ID)} if TELEGRAM_CHAT_ID else set()
_ADMIN_EXTRA = os.getenv("ADMIN_CHAT_IDS", "").strip()
if _ADMIN_EXTRA:
    ADMIN_CHAT_IDS |= {c.strip() for c in _ADMIN_EXTRA.replace(";", ",").split(",") if c.strip()}
USERS_DB = os.getenv("USERS_DB", "bot_users.db")

# ================= MULTI-USER =================
# Tanpa users.json → single-user lama (1 chat + 1 broker dari .env).
# Dengan users.json → tiap user punya chat + broker + risiko sendiri.
def _default_user():
    return {
        "id": "default",
        "telegram_chat_id": str(TELEGRAM_CHAT_ID or ""),
        "broker_mode": BROKER_MODE,
        "risk_percent": RISK_PERCENT,
        "symbol": None,  # None = ikuti active_symbol()
        "mt5_login": MT5_LOGIN,
        "mt5_password": MT5_PASSWORD,
        "mt5_server": MT5_SERVER,
        "mt5_path": MT5_PATH,
        "oanda_api_key": OANDA_API_KEY,
        "oanda_account_id": OANDA_ACCOUNT_ID,
        "oanda_env": OANDA_ENV,
        "oanda_instrument": OANDA_INSTRUMENT,
        "ctrader_access_token": CTRADER_ACCESS_TOKEN,
        "ctrader_account_id": CTRADER_ACCOUNT_ID,
        "ctrader_env": CTRADER_ENV,
        "ctrader_symbol": CTRADER_SYMBOL,
        "enabled": True,
    }


def load_users():
    """Return list dict user. Sumber: USERS_JSON env atau users.json, fallback single-user."""
    import json as _json

    raw = os.getenv("USERS_JSON", "").strip()
    if not raw:
        for _p in ("users.json",
                   os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json")):
            try:
                with open(_p, encoding="utf-8") as _f:
                    raw = _f.read().strip()
                if raw:
                    break
            except OSError:
                continue
    if not raw:
        return [_default_user()]
    try:
        data = _json.loads(raw)
    except Exception as e:
        print(f"[WARN] USERS_JSON/users.json invalid ({e}); fallback single-user.")
        return [_default_user()]
    if isinstance(data, dict):
        data = data.get("users", [data])
    users = []
    base = _default_user()
    for i, u in enumerate(data if isinstance(data, list) else []):
        if not isinstance(u, dict):
            continue
        m = dict(base)
        m.update({k: v for k, v in u.items() if v is not None})
        m["id"] = str(m.get("id") or f"user{i+1}")
        m["telegram_chat_id"] = str(m.get("telegram_chat_id") or "")
        m["broker_mode"] = str(m.get("broker_mode") or BROKER_MODE).lower()
        try:
            m["risk_percent"] = float(m.get("risk_percent", RISK_PERCENT))
        except (TypeError, ValueError):
            m["risk_percent"] = RISK_PERCENT
        if "mt5_login" in m and m["mt5_login"] not in (None, ""):
            try:
                m["mt5_login"] = int(m["mt5_login"])
            except (TypeError, ValueError):
                m["mt5_login"] = None
        m["enabled"] = bool(m.get("enabled", True))
        users.append(m)
    users = [u for u in users if u["enabled"] and u["telegram_chat_id"]]
    return users or [_default_user()]


def user_symbol(user):
    """Symbol/instrument efektif untuk user."""
    if user.get("symbol"):
        return user["symbol"]
    if (user.get("broker_mode") or "mt5") == "oanda":
        return user.get("oanda_instrument") or OANDA_INSTRUMENT
    if (user.get("broker_mode") or "mt5") == "ctrader":
        return (user.get("ctrader_symbol") or CTRADER_SYMBOL or "XAUUSD").upper()
    return SYMBOL  # mt5 + paper tampil sebagai XAUUSD
