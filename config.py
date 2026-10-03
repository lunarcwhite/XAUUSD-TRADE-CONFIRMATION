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
BROKER_MODE = os.getenv("BROKER_MODE", "mt5").strip().lower() or "mt5"

# ================= KREDENSIAL OANDA v20 =================
# Demo/testing: daftar practice di OANDA, dapat API token + Account ID.
# ENV: practice -> api-fxpractice.oanda.com, live -> api-fxtrade.oanda.com
OANDA_API_KEY = os.getenv("OANDA_API_KEY", "").strip()
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "").strip()
OANDA_ENV = os.getenv("OANDA_ENV", "practice").strip().lower() or "practice"
OANDA_INSTRUMENT = (os.getenv("OANDA_INSTRUMENT") or os.getenv("OANDA_INSTRUMENT_X") or "XAU_USD").strip() or "XAU_USD"
# Konversi lot MT5 -> units OANDA untuk XAU: 1.00 lot = 100 oz = 100 units.
OANDA_UNITS_PER_LOT = 100.0

# ================= TRADING =================
# SYMBOL dipakai untuk mode MT5. Mode OANDA pakai OANDA_INSTRUMENT (default XAU_USD).
SYMBOL = "XAUUSD"
TIMEFRAME = 15  # mt5.TIMEFRAME_M15 (tanpa import mt5 agar ringan diuji)
RISK_PERCENT = 0.01  # 1% modal per sinyal
MAGIC_NUMBER = 1002026
DEVIATION = 30  # slippage maks 30 poin ($0.30)
EXPIRY_SECONDS = 300  # timeout persetujuan Telegram 5 menit

# Lifecycle: BE + trailing (PRD S4.5)
BE_BUFFER = 0.20  # $0.20 di atas/bawah entry
TRAILING_DISTANCE = 2.00  # $2.00 di belakang harga
TRAILING_STEP = 0.50  # update SL min $0.50 (anti-spam)

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
    return SYMBOL
