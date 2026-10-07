"""SQLite journal + equity curve + laporan harian 23:55 per-mode (PRD V3.0 S5.6)."""
import os
import sqlite3
import time
from datetime import datetime, timedelta

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from config import DB_NAME, LOCAL_TZ, MAGIC_NUMBER, MODE_INTRADAY, MODE_SNIPER


def _current_mode() -> str:
    """Mode aktif global (fallback SNIPER bila state_manager tak tersedia)."""
    try:
        from state_manager import bot_state
        m = str(bot_state.current_mode or MODE_SNIPER).upper()
        return m if m in (MODE_SNIPER, MODE_INTRADAY) else MODE_SNIPER
    except Exception:
        return MODE_SNIPER


def _table_cols(conn, table) -> list:
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    except Exception:
        return []


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
    # PRD V2: catat strategi pemicu (session_sweep / trend_pullback).
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_history)")]
        if "strategy" not in cols:
            conn.execute("ALTER TABLE trade_history ADD COLUMN strategy TEXT DEFAULT ''")
    except Exception:
        pass
    # PRD V3.0 S5.6: pemisah mode (SNIPER vs INTRADAY) untuk rekap harian.
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_history)")]
        if "mode_used" not in cols:
            conn.execute("ALTER TABLE trade_history ADD COLUMN mode_used TEXT DEFAULT ''")
    except Exception:
        pass
    # Jurnal keputusan approval: approve / ignore / expire per sinyal.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS signal_decisions (
            trade_id TEXT PRIMARY KEY, user_id TEXT, strategy TEXT, regime TEXT,
            signal TEXT, entry REAL, sl REAL, tp REAL, lot REAL,
            created_at TEXT, decided_at TEXT, decision TEXT, detail TEXT)"""
    )
    # PRD V3.0: mode pada jurnal keputusan (untuk audit trail Dual-Mode).
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(signal_decisions)")]
        if "mode_used" not in cols:
            conn.execute("ALTER TABLE signal_decisions ADD COLUMN mode_used TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    conn.close()


def log_signal(trade_id, info, decision="pending", detail=""):
    """Catat/update keputusan sinyal. decision: pending/approved/ignored/expired."""
    try:
        from config import LOCAL_TZ as _TZ
        now_s = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode = str(info.get("mode") or info.get("mode_used") or _current_mode()).upper()
    try:
        init_db()
        conn = sqlite3.connect(DB_NAME)
        cols = _table_cols(conn, "signal_decisions")
        if "mode_used" in cols:
            conn.execute(
                """INSERT INTO signal_decisions
                   (trade_id, user_id, strategy, regime, signal, entry, sl, tp, lot,
                    created_at, decided_at, decision, detail, mode_used)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(trade_id) DO UPDATE SET
                     decided_at=excluded.decided_at, decision=excluded.decision,
                     detail=excluded.detail, mode_used=excluded.mode_used""",
                (str(trade_id), str(info.get("user_id", "")),
                 str(info.get("strategy", "")), str(info.get("regime", "")),
                 str(info.get("action", info.get("signal", ""))),
                 float(info.get("entry", 0) or 0), float(info.get("sl", 0) or 0),
                 float(info.get("tp", 0) or 0), float(info.get("lot", 0) or 0),
                 str(info.get("created_at", now_s)), now_s, decision, detail, mode),
            )
        else:  # DB lama tanpa mode_used
            conn.execute(
                """INSERT INTO signal_decisions
                   (trade_id, user_id, strategy, regime, signal, entry, sl, tp, lot,
                    created_at, decided_at, decision, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(trade_id) DO UPDATE SET
                     decided_at=excluded.decided_at, decision=excluded.decision,
                     detail=excluded.detail""",
                (str(trade_id), str(info.get("user_id", "")),
                 str(info.get("strategy", "")), str(info.get("regime", "")),
                 str(info.get("action", info.get("signal", ""))),
                 float(info.get("entry", 0) or 0), float(info.get("sl", 0) or 0),
                 float(info.get("tp", 0) or 0), float(info.get("lot", 0) or 0),
                 str(info.get("created_at", now_s)), now_s, decision, detail),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[WARN] jurnal keputusan: {e}")


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
            "mode_used": _current_mode(),  # atribusi mode saat sync (best-effort)
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
            strat = r.get("strategy") or ""
            mode = str(r.get("mode_used") or r.get("mode") or _current_mode()).upper()
            try:
                cur.execute(
                    """INSERT OR IGNORE INTO trade_history
                       (deal_ticket, position_ticket, symbol, trade_type, volume,
                        close_time, close_price, profit, commission, swap, net_profit, user_id, strategy, mode_used)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r["deal_ticket"], r.get("position_ticket"), r.get("symbol"),
                     r.get("trade_type"), r.get("volume"), r.get("close_time"),
                     r.get("close_price"), r.get("profit"), r.get("commission"),
                     r.get("swap"), r.get("net_profit"), uid, strat, mode),
                )
            except sqlite3.OperationalError:
                # Kompat skema transisi (tanpa mode_used) / lama.
                try:
                    cur.execute(
                        """INSERT OR IGNORE INTO trade_history
                           (deal_ticket, position_ticket, symbol, trade_type, volume,
                            close_time, close_price, profit, commission, swap, net_profit, user_id, strategy)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (r["deal_ticket"], r.get("position_ticket"), r.get("symbol"),
                         r.get("trade_type"), r.get("volume"), r.get("close_time"),
                         r.get("close_price"), r.get("profit"), r.get("commission"),
                         r.get("swap"), r.get("net_profit"), uid, strat),
                    )
                except sqlite3.OperationalError:
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
        r.setdefault("mode_used", _current_mode())
    return insert_closed_rows(rows, user_id=user_id)


def _mode_of_row(row, cols) -> str:
    """Atribusi mode 1 baris: mode_used > strategi intraday > SNIPER (legacy)."""
    try:
        if "mode_used" in cols and str(row.get("mode_used") or "").upper() in (MODE_SNIPER, MODE_INTRADAY):
            return str(row["mode_used"]).upper()
    except Exception:
        pass
    try:
        if str(row.get("strategy") or "") == "intraday_vwap_m5":
            return MODE_INTRADAY
    except Exception:
        pass
    return MODE_SNIPER


def daily_stats_by_mode(user_id=None, day=None, db_path=None):
    """Statistik {SNIPER: {n,wins,pnl,wr}, INTRADAY: {...}} hari `day` (PRD 5.6).

    Fail-open: DB/tabel hilang -> kedua mode nol.
    """
    try:
        from config import DB_NAME as _DB, LOCAL_TZ as _TZ
        day = day or datetime.now(_TZ).strftime("%Y-%m-%d")
        path = db_path or _DB
    except Exception:
        return {m: {"n": 0, "wins": 0, "pnl": 0.0, "wr": 0.0}
                for m in (MODE_SNIPER, MODE_INTRADAY)}
    stats = {m: {"n": 0, "wins": 0, "pnl": 0.0, "wr": 0.0}
             for m in (MODE_SNIPER, MODE_INTRADAY)}
    try:
        conn = sqlite3.connect(path)
        try:
            cols = _table_cols(conn, "trade_history")
            if not cols:
                return stats
            q = "SELECT net_profit, mode_used, strategy FROM trade_history WHERE close_time LIKE ?"
            params: list = [day + "%"]
            if user_id is not None and "user_id" in cols:
                q += " AND user_id=?"
                params.append(str(user_id))
            cur = conn.execute(q, params)
            col_names = [d[0] for d in cur.description]
            for tup in cur.fetchall():
                row = dict(zip(col_names, tup))
                m = _mode_of_row(row, cols)
                try:
                    pnl = float(row.get("net_profit") or 0.0)
                except Exception:
                    pnl = 0.0
                stats[m]["n"] += 1
                stats[m]["pnl"] += pnl
                if pnl > 0:
                    stats[m]["wins"] += 1
        finally:
            conn.close()
    except Exception:
        return stats
    for m in stats:
        n = stats[m]["n"]
        stats[m]["wr"] = (stats[m]["wins"] / n * 100) if n else 0.0
        stats[m]["pnl"] = round(stats[m]["pnl"], 2)
    return stats


def format_mode_breakdown(stats) -> str:
    """Baris rekap per-mode untuk caption Telegram."""
    try:
        s, i = stats[MODE_SNIPER], stats[MODE_INTRADAY]
        return (f"🔹 SNIPER: `{s['n']}` trade · WR `{s['wr']:.0f}%` · `{s['pnl']:+.2f}`\n"
                f"🔸 INTRADAY: `{i['n']}` trade · WR `{i['wr']:.0f}%` · `{i['pnl']:+.2f}`")
    except Exception:
        return ""


def generate_equity_curve(initial_balance=1000.0, filename="equity_curve.png", user_id=None):
    """Kurva equity + drawdown% + garis per-mode (PRD 5.6). user_id=None = semua user."""
    conn = sqlite3.connect(DB_NAME)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_history)")]
        want_mode = "mode_used" in cols
        sel = "SELECT close_time, net_profit" + (", mode_used, strategy" if want_mode or "strategy" in cols else "")
        if user_id and "user_id" in cols:
            df = pd.read_sql_query(
                sel + " FROM trade_history WHERE user_id=? ORDER BY close_time",
                conn, params=(user_id,))
        else:
            df = pd.read_sql_query(
                sel + " FROM trade_history ORDER BY close_time", conn)
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
    # Garis per-mode (SNIPER oranye, INTRADAY hijau) bila atribusi tersedia.
    try:
        if "mode_used" in df.columns or "strategy" in df.columns:
            col_list = list(df.columns)
            for m, color in ((MODE_SNIPER, "#FF9800"), (MODE_INTRADAY, "#66BB6A")):
                sub = df[[_mode_of_row(r, col_list) == m for _, r in df.iterrows()]]
                if not sub.empty:
                    sub = sub.copy()
                    sub["close_time"] = pd.to_datetime(sub["close_time"])
                    sub = sub.sort_values("close_time")
                    sub_eq = initial_balance + sub["net_profit"].astype(float).cumsum()
                    ax1.plot(sub["close_time"], sub_eq, color=color, linewidth=1.2,
                             linestyle="--", alpha=0.9,
                             label=f"{m} ({sub['net_profit'].sum():+.0f})")
    except Exception as e:
        print(f"[WARN] kurva per-mode: {e}")
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
            breakdown = format_mode_breakdown(daily_stats_by_mode(day=now.strftime("%Y-%m-%d")))
            caption = (
                f"📋 *LAPORAN HARIAN XAU/USD* `{now:%d %B %Y}`\n━━━━\n"
                f"🔢 Trade: `{n}` | ✅ `{wins}` | 🎯 WR `{wr:.1f}%`\n"
                f"{'🟢' if pnl >= 0 else '🔴'} *PnL hari ini:* `{pnl:+.2f}`\n"
                f"{breakdown}\n━━━━\n"
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
            breakdown = format_mode_breakdown(
                daily_stats_by_mode(user_id=user_id, day=now.strftime("%Y-%m-%d")))
            caption = (
                f"📋 *LAPORAN HARIAN XAU/USD [{label}]* `{now:%d %B %Y}`\n━━━━\n"
                f"🔢 Trade: `{n}` | ✅ `{wins}` | 🎯 WR `{wr:.1f}%`\n"
                f"{'🟢' if pnl >= 0 else '🔴'} *PnL hari ini:* `{pnl:+.2f}`\n"
                f"{breakdown}\n━━━━\n"
                f"📈 Total PnL: `{total_pnl:+.2f}` | Max DD: `{max_dd:.2f}%`\n"
                f"🏦 Saldo: `${balance:,.2f}`")
            if img and os.path.exists(img):
                send_photo(img, caption)
                os.remove(img)
            else:
                send_text(caption)
            last_day = now.date()
        time.sleep(30)
