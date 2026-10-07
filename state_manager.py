"""Module: state_manager.py - Pengelola Mode Dual-Mode & Rem Darurat Harian (PRD V3.0 S5.1).

Mode:
- SNIPER (default, M15): selektif, tanpa kuota kaku, overnight diizinkan.
- INTRADAY (M5): kuota 3/hari, kill-switch 2%, tolak entri >= 22:30 WIB,
  auto-flat 23:00 WIB.

Isolasi kegagalan: import MT5 lazy (tak mematikan paper/OANDA/cTrader),
DB hilang/rusak -> fail-open (0 trade, 0 pnl) agar tak crash loop utama.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime

from config import (
    DB_PATH,
    FORCE_FLAT_HOUR,
    INTRADAY_KILL_LOSS_PCT,
    INTRADAY_MAX_TRADES_PER_DAY,
    INTRADAY_NO_ENTRY_HOUR,
    INTRADAY_NO_ENTRY_MINUTE,
    LOCAL_TZ,
    MAGIC_NUMBER,
    MODE_INTRADAY,
    MODE_SNIPER,
    drift_for_mode,
    expiry_for_mode,
    is_intraday_entry_closed,
    trailing_for_mode,
)

# Kompat impor lama: konstanta level modul tetap tersedia.
DAILY_MAX_TRADES = INTRADAY_MAX_TRADES_PER_DAY  # 3 (PRD S4)
DAILY_MAX_LOSS_PCT = INTRADAY_KILL_LOSS_PCT  # 0.02 (PRD S4)


def _table_columns(conn, table="trade_history"):
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    except Exception:
        return []


class BotStateManager:

    def __init__(self, initial_mode=MODE_SNIPER):
        m = str(initial_mode or MODE_SNIPER).upper()
        self.current_mode = m if m in (MODE_SNIPER, MODE_INTRADAY) else MODE_SNIPER
        self.kill_switch_active = False
        self._last_flat_date = None  # kompat: tanggal flat terakhir (kunci global)
        self._last_flat_by_key: dict = {}  # tanggal flat per sesi (multi-user)
        self._lock = threading.Lock()

    # ---------- Mode ----------

    def set_mode(self, mode_name: str) -> str:
        """Mengganti mode operasional bot."""
        mode_upper = str(mode_name or "").strip().upper()
        if mode_upper in (MODE_SNIPER, MODE_INTRADAY):
            with self._lock:
                self.current_mode = mode_upper
                if mode_upper == MODE_SNIPER:
                    self.kill_switch_active = False
            return f"✅ Mode bot berhasil diubah menjadi: *{self.current_mode}*"
        return "❌ Mode tidak valid! Gunakan: `SNIPER` atau `INTRADAY`"

    def parse_mode_command(self, text: str) -> tuple[bool, str]:
        """Endpoint /mode <sniper|intraday> (PRD 5.1). Return (changed, reply)."""
        parts = str(text or "").strip().split()
        # Dukung "/mode intraday", "mode sniper", "/mode@bot intraday".
        if parts and parts[0].startswith("/mode"):
            arg = parts[1] if len(parts) > 1 else ""
        elif parts and parts[0].lower() == "mode" and len(parts) > 1:
            arg = parts[1]
        else:
            arg = parts[0] if parts else ""
        if not arg:
            return False, "Gunakan: `/mode <sniper|intraday>`"
        before = self.current_mode
        reply = self.set_mode(arg)
        return (self.current_mode != before and "berhasil" in reply), reply

    def expiry_seconds(self) -> int:
        return expiry_for_mode(self.current_mode)

    def max_drift(self) -> float:
        return drift_for_mode(self.current_mode)

    def trailing_distance(self) -> float:
        return trailing_for_mode(self.current_mode)

    # ---------- Statistik harian ----------

    def get_today_stats(self, user_id=None, day=None, db_path=None,
                        mode=None):
        """(total_trades, net_pnl) hari ini dari SQLite. Fail-open (0, 0.0).

        - mode='INTRADAY' + kolom mode_used/strategy ada -> hitung hanya
          trade INTRADAY (untuk kuota). Tanpa kolom -> hitung semua.
        - user_id bila kolom user_id ada -> filter per user.
        """
        try:
            day_s = day or datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
        except Exception:
            day_s = datetime.now().strftime("%Y-%m-%d")
        path = db_path or DB_PATH
        try:
            conn = sqlite3.connect(path)
        except Exception:
            return 0, 0.0
        try:
            try:
                conn.execute("SELECT 1 FROM trade_history LIMIT 1")
            except Exception:
                return 0, 0.0  # tabel belum ada (fresh install)
            cols = _table_columns(conn)
            q = "SELECT COUNT(*), COALESCE(SUM(net_profit),0) FROM trade_history WHERE close_time LIKE ?"
            params: list = [f"{day_s}%"]
            if user_id is not None and "user_id" in cols:
                q += " AND user_id=?"
                params.append(str(user_id))
            if mode is not None:
                if "mode_used" in cols:
                    q += " AND mode_used=?"
                    params.append(str(mode).upper())
                elif "strategy" in cols and str(mode).upper() == MODE_INTRADAY:
                    # Kompat DB lama: strategi intraday = intraday_vwap_m5.
                    q += " AND strategy=?"
                    params.append("intraday_vwap_m5")
            row = conn.execute(q, params).fetchone()
            total = int(row[0] or 0) if row else 0
            pnl = float(row[1] or 0.0) if row else 0.0
            return total, pnl
        except Exception:
            return 0, 0.0
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ---------- Validasi INTRADAY (PRD 5.1) ----------

    def validate_daily_intraday_rules(self, balance=None, total_trades=None,
                                      net_pnl=None, now=None, user_id=None,
                                      db_path=None) -> tuple[bool, str]:
        """Pemeriksaan wajib sebelum sinyal INTRADAY keluar.

        Param opsional (balance/total/net/now) untuk injeksi broker multi-user
        & unit test tanpa MT5. Bila None -> ambil otomatis (MT5 + SQLite).
        """
        try:
            now = now or datetime.now(LOCAL_TZ)
        except Exception:
            now = datetime.now()

        # 1. Rem Waktu: dilarang buka posisi >= 22:30 WIB.
        try:
            if is_intraday_entry_closed(now):
                return (False, "Waktu operasional harian berakhir (Mendekati jam 23:00 WIB)")
        except Exception:
            pass
        # Kompat logika lama: hour>=23 atau (22:xx >= 30).
        try:
            if now.hour >= 23 or (now.hour == INTRADAY_NO_ENTRY_HOUR
                                 and now.minute >= INTRADAY_NO_ENTRY_MINUTE):
                return (False, "Waktu operasional harian berakhir (Mendekati jam 23:00 WIB)")
        except Exception:
            pass

        # 2. Saldo akun (injeksi untuk multi-user/test, fallback MT5 lazy).
        if balance is None:
            try:
                import MetaTrader5 as mt5

                acct = mt5.account_info()
                balance = float(acct.balance) if acct else 10000.0
            except Exception:
                balance = 10000.0
        try:
            balance = float(balance or 0.0)
        except Exception:
            balance = 10000.0

        # 3. Statistik harian (injeksi atau SQLite; filter INTRADAY bila kolom ada).
        if total_trades is None or net_pnl is None:
            t, p = self.get_today_stats(user_id=user_id, db_path=db_path,
                                        mode=MODE_INTRADAY)
            total_trades = t if total_trades is None else total_trades
            net_pnl = p if net_pnl is None else net_pnl
        try:
            total_trades = int(total_trades or 0)
            net_pnl = float(net_pnl or 0.0)
        except Exception:
            total_trades, net_pnl = 0, 0.0

        # 4. Rem Kuota Transaksi Harian (maks 3).
        if total_trades >= INTRADAY_MAX_TRADES_PER_DAY:
            return (False,
                    f"Batas kuota harian tercapai ({total_trades}/{INTRADAY_MAX_TRADES_PER_DAY} trade)")

        # 5. Rem Darurat Kerugian Maksimal (Kill Switch 2%).
        max_loss_allowed = balance * INTRADAY_KILL_LOSS_PCT
        if net_pnl < 0 and abs(net_pnl) >= max_loss_allowed:
            with self._lock:
                self.kill_switch_active = True
            return (False,
                    f"KILL SWITCH AKTIF: Kerugian harian (-${abs(net_pnl):.2f}) mencapai batas 2%!")

        with self._lock:
            self.kill_switch_active = False  # kondisi pulih (hari baru) -> buka kunci
        return True, "Semua parameter checklist harian terpenuhi"

    def should_block_signal(self, user_id=None, db_path=None, now=None,
                            balance=None) -> tuple[bool, str]:
        """Dispatcher: SNIPER selalu lolos rem harian; INTRADAY via validasi."""
        if str(self.current_mode).upper() == MODE_INTRADAY:
            ok, why = self.validate_daily_intraday_rules(
                balance=balance, now=now, user_id=user_id, db_path=db_path)
            return (not ok), why
        return False, "SNIPER: tanpa kuota/kill-switch"

    # ---------- Auto-Flat 23:00 WIB (PRD 5.1) ----------

    def should_auto_flat(self, now=None, key="global") -> bool:
        """True bila mode INTRADAY dan jam 23:00 WIB (jendela 23:00, sekali sehari per sesi)."""
        if str(self.current_mode).upper() != MODE_INTRADAY:
            return False
        try:
            now = now or datetime.now(LOCAL_TZ)
        except Exception:
            now = datetime.now()
        try:
            if now.hour != FORCE_FLAT_HOUR:
                return False
            with self._lock:
                last = self._last_flat_by_key.get(key, self._last_flat_date)
            if last == now.date():
                return False  # sesi ini sudah flat hari ini
            return True
        except Exception:
            return False

    def close_all_positions(self, broker=None, symbol=None):
        """Tutup seluruh posisi bot. Return (closed, failed, details).

        Broker-agnostic: coba broker.partial_close(ratio=1.0) / close_position
        bila ada, fallback ke MT5 raw (filter MAGIC_NUMBER).
        """
        closed, failed, details = 0, 0, []
        # Jalur 1: via adapter broker (paper/OANDA/cTrader/MT5).
        if broker is not None:
            try:
                positions = broker.list_positions(symbol) if symbol else broker.list_positions()
            except Exception as e:
                return 0, 0, [f"list gagal: {e}"]
            for pos in positions or []:
                ticket = getattr(pos, "ticket", "?")
                psym = getattr(pos, "symbol", symbol or "?")
                try:
                    if hasattr(broker, "close_position"):
                        ok = broker.close_position(pos)
                        ok = bool(ok if isinstance(ok, bool) else ok[0])
                    else:
                        ok, _ = broker.partial_close(pos, 1.0)
                        if not ok:
                            # Fallback MT5 raw untuk sisa yang tak bisa partial penuh.
                            ok = self._mt5_close_raw(ticket)
                    if ok:
                        closed += 1
                        details.append(f"#{ticket} {psym} flat OK")
                    else:
                        failed += 1
                        details.append(f"#{ticket} {psym} flat GAGAL")
                except Exception as e:
                    failed += 1
                    details.append(f"#{ticket} flat error: {e}")
            return closed, failed, details
        # Jalur 2: MT5 raw (kompat single-user lama).
        return self._mt5_flat_all()

    def _mt5_close_raw(self, ticket) -> bool:
        try:
            import MetaTrader5 as mt5

            poss = mt5.positions_get() or []
            live = next((p for p in poss if str(p.ticket) == str(ticket)), None)
            if live is None:
                return False
            tick = mt5.symbol_info_tick(live.symbol)
            if not tick:
                return False
            is_buy = live.type == mt5.POSITION_TYPE_BUY
            from config import DEVIATION

            def _filling(sym):
                info = mt5.symbol_info(sym)
                if not info:
                    return mt5.ORDER_FILLING_RETURN
                if info.filling_mode & mt5.ORDER_FILLING_IOC:
                    return mt5.ORDER_FILLING_IOC
                if info.filling_mode & mt5.ORDER_FILLING_FOK:
                    return mt5.ORDER_FILLING_FOK
                return mt5.ORDER_FILLING_RETURN

            r = mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL, "position": live.ticket,
                "symbol": live.symbol, "volume": live.volume,
                "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "price": tick.bid if is_buy else tick.ask,
                "deviation": DEVIATION, "magic": MAGIC_NUMBER,
                "comment": "Auto-Flat 23:00",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": _filling(live.symbol),
            })
            return bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)
        except Exception:
            return False

    def _mt5_flat_all(self):
        closed, failed, details = 0, 0, []
        try:
            import MetaTrader5 as mt5
        except Exception as e:
            return 0, 0, [f"MT5 tak tersedia: {e}"]
        try:
            positions = [p for p in (mt5.positions_get() or [])
                         if p.magic == MAGIC_NUMBER]
        except Exception as e:
            return 0, 0, [f"positions_get gagal: {e}"]
        for p in positions:
            ok = self._mt5_close_raw(p.ticket)
            if ok:
                closed += 1
                details.append(f"#{p.ticket} {p.symbol} flat OK")
            else:
                failed += 1
                details.append(f"#{p.ticket} {p.symbol} flat GAGAL")
        return closed, failed, details

    def start_auto_flat_thread(self, broker=None, send_text=None, symbol=None,
                               stop_event=None, check_interval=15):
        """Thread daemon: tiap check_interval cek should_auto_flat -> flat + notif."""

        def _loop():
            print("🕚 Auto-Flat 23:00 WIB aktif (INTRADAY)...")
            flat_key = f"{getattr(broker, 'name', 'mt5')}:{symbol or '-'}"
            while stop_event is None or not stop_event.is_set():
                try:
                    if self.should_auto_flat(key=flat_key):
                        try:
                            from datetime import date as _d
                            today = datetime.now(LOCAL_TZ).date()
                        except Exception:
                            today = datetime.now().date()
                        closed, failed, details = self.close_all_positions(
                            broker=broker, symbol=symbol)
                        with self._lock:
                            self._last_flat_by_key[flat_key] = today
                            self._last_flat_date = today
                        msg = (f"🕚 *AUTO-FLAT 23:00 [{self.current_mode}]*\n"
                               f"Flat `{closed}` posisi"
                               + (f", gagal `{failed}`" if failed else "")
                               + (f"\n" + "\n".join(f"• {d}" for d in details[:10])
                                  if details else "\nTidak ada posisi terbuka."))
                        if send_text:
                            try:
                                send_text(msg)
                            except Exception as e:
                                print(f"[WARN] notif auto-flat: {e}")
                        print(f"[AUTO-FLAT] closed={closed} failed={failed}")
                except Exception as e:
                    print(f"[ERROR] auto-flat: {e}")
                try:
                    if stop_event is not None and stop_event.wait(check_interval):
                        break
                    else:
                        time.sleep(check_interval)
                except Exception:
                    time.sleep(check_interval)

        th = threading.Thread(target=_loop, daemon=True)
        th.start()
        return th


# Singleton instance
bot_state = BotStateManager()
