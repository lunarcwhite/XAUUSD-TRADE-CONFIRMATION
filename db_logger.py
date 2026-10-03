"""SQLite journal + equity curve + laporan harian 23:55 (PRD S4.6)."""
import os
import sqlite3
import time
from datetime import datetime, timedelta

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from config import DB_NAME, LOCAL_TZ, MAGIC_NUMBER


def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS trade_history (
            deal_ticket INTEGER PRIMARY KEY, position_ticket INTEGER,
            symbol TEXT, trade_type TEXT, volume REAL, close_time TEXT,
            close_price REAL, profit REAL, commission REAL, swap REAL,
            net_profit REAL)"""
    )
    # Migrasi multi-user: tambah user_id bila belum ada.
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_history)")]
        if "user_id" not in cols:
            conn.execute("ALTER TABLE trade_history ADD COLUMN user_id TEXT DEFAULT 'default'")
    except Exception:
        pass
    conn.commit()
    conn.close()


def sync_closed_deals_to_sqlite(days_back=7):
    """Tarik DEAL_ENTRY_OUT milik bot ke SQLite. Return jumlah baris baru (MT5)."""
    import MetaTrader5 as mt5

    deals = mt5.history_deals_get(datetime.now() - timedelta(days=days_back),
                                  datetime.now())
    if not deals:
        return 0
    rows = []
    for d in deals:
        if d.magic != MAGIC_NUMBER or d.entry != mt5.DEAL_ENTRY_OUT:
            continue
        rows.append({
            "deal_ticket": d.ticket, "position_ticket": d.position_id, "symbol": d.symbol,
            "trade_type": "BUY (Close)" if d.type == mt5.DEAL_TYPE_SELL else "SELL (Close)",
            "volume": d.volume,
            "close_time": datetime.fromtimestamp(d.time, tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "close_price": d.price, "profit": d.profit, "commission": d.commission,
            "swap": d.swap,
            "net_profit": round(d.profit + d.commission + d.swap, 2),
        })
    return insert_closed_rows(rows)


def insert_closed_rows(rows, user_id="default"):
    """Insert list dict closed-trade ke SQLite. Return jumlah baris baru."""
    if not rows:
        return 0
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    n = 0
    for r in rows:
        try:
            uid = r.get("user_id") or user_id
            try:
                cur.execute(
                    """INSERT OR IGNORE INTO trade_history
                       (deal_ticket, position_ticket, symbol, trade_type, volume,
                        close_time, close_price, profit, commission, swap, net_profit, user_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r["deal_ticket"], r.get("position_ticket"), r.get("symbol"),
                     r.get("trade_type"), r.get("volume"), r.get("close_time"),
                     r.get("close_price"), r.get("profit"), r.get("commission"),
                     r.get("swap"), r.get("net_profit"), uid),
                )
            except sqlite3.OperationalError:
                # DB lama tanpa kolom user_id
                cur.execute(
                    """INSERT OR IGNORE INTO trade_history VALUES
                       (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r["deal_ticket"], r.get("position_ticket"), r.get("symbol"),
                     r.get("trade_type"), r.get("volume"), r.get("close_time"),
                     r.get("close_price"), r.get("profit"), r.get("commission"),
                     r.get("swap"), r.get("net_profit")),
                )
            n += cur.rowcount
        except Exception:
            continue
    conn.commit()
    conn.close()
    return n


def sync_via_broker(broker, days_back=7, user_id="default"):
    """Sync generik MT5/OANDA via broker.fetch_closed()."""
    try:
        rows = broker.fetch_closed(days_back=days_back) or []
    except Exception as e:
        print(f"[ERROR] sync broker: {e}")
        return 0
    for r in rows:
        r.setdefault("user_id", user_id)
    return insert_closed_rows(rows, user_id=user_id)


def generate_equity_curve(initial_balance=1000.0, filename="equity_curve.png", user_id=None):
    """Kurva equity + drawdown%. user_id=None = semua user (kompat lama)."""
    conn = sqlite3.connect(DB_NAME)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_history)")]
        if user_id and "user_id" in cols:
            df = pd.read_sql_query(
                "SELECT close_time, net_profit FROM trade_history WHERE user_id=? ORDER BY close_time",
                conn, params=(user_id,))
        else:
            df = pd.read_sql_query(
                "SELECT close_time, net_profit FROM trade_history ORDER BY close_time",
                conn)
    finally:
        conn.close()
    if df.empty:
        print("[WARN] DB kosong, kurva batal.")
        return None, 0.0, 0.0
    df["close_time"] = pd.to_datetime(df["close_time"])
    df["equity"] = initial_balance + df["net_profit"].cumsum()
    df["drawdown"] = (df["equity"] - df["equity"].cummax()) / df["equity"].cummax() * 100
    total_pnl, max_dd, final_eq = df["net_profit"].sum(), abs(df["drawdown"].min()), df["equity"].iloc[-1]
    plot = pd.concat([pd.DataFrame(
        {"close_time": [df["close_time"].iloc[0] - pd.Timedelta(hours=1)],
         "equity": [initial_balance], "drawdown": [0.0]}), df],
        ignore_index=True)

    plt.style.use("dark_background")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6.5),
                                   gridspec_kw={"height_ratios": [3, 1]},
                                   sharex=True)
    fig.patch.set_facecolor("#121417")
    for ax in (ax1, ax2):
        ax.set_facecolor("#1a1d24")
        ax.grid(True, linestyle=":", alpha=0.3, color="#546E7A")
    ax1.plot(plot["close_time"], plot["equity"], color="#29B6F6",
             linewidth=2, label=f"Equity (${final_eq:,.2f})")
    ax1.fill_between(plot["close_time"], plot["equity"], initial_balance,
                     alpha=0.15, color="#29B6F6")
    ax1.axhline(initial_balance, color="#78909C", linestyle="--", linewidth=1)
    ax1.set_title("XAU/USD Performance: Equity Curve & Drawdown",
                  fontsize=13, fontweight="bold", color="#ECEFF1")
    ax1.set_ylabel("Balance ($)", color="#CFD8DC")
    ax1.legend(loc="upper left", framealpha=0.4)
    ax2.plot(plot["close_time"], plot["drawdown"], color="#EF5350",
             label=f"Max DD: {max_dd:.2f}%")
    ax2.fill_between(plot["close_time"], plot["drawdown"], 0,
                     alpha=0.3, color="#EF5350")
    ax2.set_ylabel("Drawdown (%)", color="#CFD8DC", fontsize=9)
    ax2.legend(loc="lower left", framealpha=0.4)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b"))
    fig.autofmt_xdate()
    plt.tight_layout()
    plt.savefig(filename, dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close()
    return filename, total_pnl, max_dd


def daily_reporter(send_text, send_photo):
    """Sync DB → kurva → kirim rekap. Trigger 23:55 WIB sekali sehari (MT5)."""
    init_db()
    print("📊 Daily reporter aktif (23:55 WIB)...")
    last_day = None
    while True:
        now = datetime.now(LOCAL_TZ)
        if now.hour == 23 and now.minute >= 55 and last_day != now.date():
            print("[REPORT] Menyusun laporan...")
            new = sync_closed_deals_to_sqlite(days_back=2)
            print(f"[DB] {new} transaksi baru.")
            try:
                import MetaTrader5 as mt5

                acct = mt5.account_info()
                balance = acct.balance if acct else 0.0
            except Exception:
                balance = 0.0
            img, total_pnl, max_dd = generate_equity_curve()
            conn = sqlite3.connect(DB_NAME)
            today = pd.read_sql_query(
                "SELECT net_profit FROM trade_history WHERE close_time LIKE ?",
                conn, params=(now.strftime("%Y-%m-%d") + "%",))
            conn.close()
            n, wins = len(today), int((today["net_profit"] > 0).sum())
            pnl = float(today["net_profit"].sum()) if n else 0.0
            wr = wins / n * 100 if n else 0.0
            caption = (
                f"📋 *LAPORAN HARIAN XAU/USD* `{now:%d %B %Y}`\n━━━━\n"
                f"🔢 Trade: `{n}` | ✅ `{wins}` | 🎯 WR `{wr:.1f}%`\n"
                f"{'🟢' if pnl >= 0 else '🔴'} *PnL hari ini:* `{pnl:+.2f}`\n━━━━\n"
                f"📈 Total PnL: `{total_pnl:+.2f}` | Max DD: `{max_dd:.2f}%`\n"
                f"🏦 Saldo: `${balance:,.2f}`")
            if img and os.path.exists(img):
                send_photo(img, caption)
                os.remove(img)
            else:
                send_text(caption)
            last_day = now.date()
        time.sleep(30)


def daily_reporter_generic(broker, send_text, send_photo, user_id="default", stop_event=None):
    """Reporter generik MT5/OANDA per-user. Sync via broker.fetch_closed()."""
    init_db()
    label = getattr(broker, "name", "broker").upper()
    print(f"📊 Daily reporter aktif [{label}:{user_id}] (23:55 WIB)...")
    last_day = None
    while stop_event is None or not stop_event.is_set():
        now = datetime.now(LOCAL_TZ)
        if now.hour == 23 and now.minute >= 55 and last_day != now.date():
            print(f"[REPORT:{user_id}] Menyusun laporan...")
            new = sync_via_broker(broker, days_back=2, user_id=user_id)
            print(f"[DB:{user_id}] {new} transaksi baru.")
            try:
                balance = float(broker.get_balance() or 0.0)
            except Exception:
                balance = 0.0
            img, total_pnl, max_dd = generate_equity_curve(user_id=user_id,
                                                           filename=f"equity_{user_id}.png")
            conn = sqlite3.connect(DB_NAME)
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_history)")]
                if "user_id" in cols:
                    today = pd.read_sql_query(
                        "SELECT net_profit FROM trade_history WHERE close_time LIKE ? AND user_id=?",
                        conn, params=(now.strftime("%Y-%m-%d") + "%", user_id))
                else:
                    today = pd.read_sql_query(
                        "SELECT net_profit FROM trade_history WHERE close_time LIKE ?",
                        conn, params=(now.strftime("%Y-%m-%d") + "%",))
            finally:
                conn.close()
            n, wins = len(today), int((today["net_profit"] > 0).sum())
            pnl = float(today["net_profit"].sum()) if n else 0.0
            wr = wins / n * 100 if n else 0.0
            caption = (
                f"📋 *LAPORAN HARIAN XAU/USD [{label}]* `{now:%d %B %Y}`\n━━━━\n"
                f"🔢 Trade: `{n}` | ✅ `{wins}` | 🎯 WR `{wr:.1f}%`\n"
                f"{'🟢' if pnl >= 0 else '🔴'} *PnL hari ini:* `{pnl:+.2f}`\n━━━━\n"
                f"📈 Total PnL: `{total_pnl:+.2f}` | Max DD: `{max_dd:.2f}%`\n"
                f"🏦 Saldo: `${balance:,.2f}`")
            if img and os.path.exists(img):
                send_photo(img, caption)
                os.remove(img)
            else:
                send_text(caption)
            last_day = now.date()
        time.sleep(30)
