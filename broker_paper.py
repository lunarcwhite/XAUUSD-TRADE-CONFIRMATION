"""Adapter PAPER/simulasi (tanpa broker, tanpa API key).

Feed M15: Yahoo Finance futures emas GC=F (COMEX, tanpa auth, di-label XAUUSD).
Eksekusi: fill simulasi di memori + sentuh SL/TP per bar + jurnal untuk ukur win rate.
Uang: virtual PAPER_BALANCE. JANGAN dipakai untuk akun real.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from broker_base import BrokerAdapter, NormalizedPosition, Tick
from config import LOCAL_TZ

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
CONTRACT_PER_LOT = 100.0  # 1.00 lot XAU = 100 oz


class PaperAdapter(BrokerAdapter):
    name = "paper"

    def __init__(self, balance=None, risk_percent=None, yahoo_symbol=None):
        from config import PAPER_BALANCE, PAPER_YAHOO_SYMBOL

        self.initial_balance = float(balance or PAPER_BALANCE or 10000.0)
        self.risk_percent = risk_percent
        self.yahoo_symbol = yahoo_symbol or PAPER_YAHOO_SYMBOL
        self._lock = threading.Lock()
        self._seq = 0
        self._opens: dict[str, dict] = {}
        self._closed: list[dict] = []
        self._last_close: float = 0.0

    # ---------- koneksi (selalu ok) ----------
    def initialize(self) -> bool:
        df = self.get_rates_m15("XAUUSD", 100)
        if df is None or len(df) < 60:
            print("[ERROR] paper feed Yahoo tak cukup.")
            return False
        print(f"[PAPER] feed GC=F ok, saldo virtual ${self.get_balance():,.2f}")
        return True

    # ---------- data ----------
    def get_rates_m15(self, symbol: str = "XAUUSD", count: int = 100):
        try:
            r = requests.get(
                YAHOO_URL, params={"interval": "15m", "range": "5d"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            ts = res["timestamp"]
            q = res["indicators"]["quote"][0]
            rows = []
            for i in range(len(ts)):
                c, o, h, low = q["close"][i], q["open"][i], q["high"][i], q["low"][i]
                if c is None or o is None or h is None or low is None:
                    continue  # bar belum terbentuk / market tutup
                rows.append({
                    "time": int(ts[i]),
                    "open": float(o), "high": float(h),
                    "low": float(low), "close": float(c),
                    "tick_volume": int((q.get("volume") or [0])[i] or 0),
                })
            if len(rows) < 60:
                return None
            df = pd.DataFrame(rows[-int(count):]).reset_index(drop=True)
            with self._lock:
                self._last_close = float(df.iloc[-1]["close"])
            self._simulate_fills(df)
            return df
        except Exception as e:
            print(f"[ERROR] paper feed: {e}")
            return None

    def get_balance(self) -> float:
        with self._lock:
            realized = sum(r.get("net_profit", 0.0) for r in self._closed)
            return round(self.initial_balance + realized, 2)

    def get_tick(self, symbol: str = "XAUUSD") -> Tick | None:
        with self._lock:
            px = self._last_close
        if not px:
            df = self.get_rates_m15(symbol, 5)
            if df is None:
                return None
            px = float(df.iloc[-1]["close"])
        return Tick(bid=round(px - 0.15, 2), ask=round(px + 0.15, 2))

    # ---------- order simulasi ----------
    def market_order(self, symbol: str, action: str, lot: float, sl: float, tp: float):
        tick = self.get_tick(symbol)
        if not tick:
            return False, "Feed paper tak tersedia"
        entry = tick.ask if action == "BUY" else tick.bid
        with self._lock:
            self._seq += 1
            ticket = f"P{int(time.time())}_{self._seq}"
            self._opens[ticket] = {
                "symbol": symbol or "XAUUSD", "side": action,
                "volume_lot": float(lot), "price_open": entry,
                "sl": float(sl), "tp": float(tp),
                "opened_at": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            }
        return True, f"PAPER {action} {lot} @ {entry:.2f} (#{ticket})"

    def list_positions(self, symbol: str | None = None) -> list[NormalizedPosition]:
        with self._lock:
            items = list(self._opens.items())
        out = []
        for ticket, p in items:
            if symbol and p["symbol"] != symbol:
                continue
            out.append(NormalizedPosition(
                ticket=ticket, symbol=p["symbol"], side=p["side"],
                volume_lot=p["volume_lot"], price_open=p["price_open"],
                sl=p["sl"], tp=p["tp"]))
        return out

    def modify_sltp(self, ticket, symbol: str, new_sl: float, new_tp: float) -> bool:
        with self._lock:
            p = self._opens.get(str(ticket))
            if not p:
                return False
            p["sl"], p["tp"] = float(new_sl), float(new_tp)
            return True

    def partial_close(self, pos: NormalizedPosition, ratio: float = 0.5):
        tick = self.get_tick(pos.symbol)
        if not tick:
            return False, 0.0
        px = tick.bid if pos.side == "BUY" else tick.ask
        with self._lock:
            p = self._opens.get(str(pos.ticket))
            if not p or p["volume_lot"] < 0.02:
                return False, 0.0
            closed_lot = round(p["volume_lot"] * ratio, 2)
            if closed_lot < 0.01 or (p["volume_lot"] - closed_lot) < 0.01:
                return False, 0.0
            direction = 1 if p["side"] == "BUY" else -1
            pnl = round((px - p["price_open"]) * direction * closed_lot * CONTRACT_PER_LOT, 2)
            p["volume_lot"] = round(p["volume_lot"] - closed_lot, 2)
            self._closed.append({
                "deal_ticket": abs(hash((str(pos.ticket), closed_lot, time.time()))) % (10 ** 9),
                "position_ticket": str(pos.ticket),
                "symbol": p["symbol"],
                "trade_type": f"{p['side']} (Partial)",
                "volume": closed_lot,
                "close_time": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "close_price": px, "profit": pnl, "commission": 0.0,
                "swap": 0.0, "net_profit": pnl,
            })
            return True, closed_lot

    def fetch_closed(self, days_back: int = 7) -> list[dict]:
        cutoff = datetime.now(LOCAL_TZ) - timedelta(days=days_back)
        with self._lock:
            rows = list(self._closed)
        out = []
        for r in rows:
            try:
                if datetime.strptime(r["close_time"], "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=LOCAL_TZ) >= cutoff:
                    out.append(dict(r))
            except Exception:
                out.append(dict(r))
        return out

    # ---------- simulasi sentuh SL/TP pada bar terakhir ----------
    def _simulate_fills(self, df):
        try:
            bar = df.iloc[-1]
            hi, low = float(bar["high"]), float(bar["low"])
        except Exception:
            return
        done = []
        with self._lock:
            for ticket, p in self._opens.items():
                is_buy = p["side"] == "BUY"
                hit_sl = (low <= p["sl"]) if is_buy else (hi >= p["sl"])
                hit_tp = (hi >= p["tp"]) if is_buy else (low <= p["tp"])
                if not (hit_sl or hit_tp):
                    continue
                # Konservatif: bila satu bar sentuh keduanya, SL diproses dulu.
                close_px = p["sl"] if hit_sl else p["tp"]
                direction = 1 if is_buy else -1
                pnl = round((close_px - p["price_open"]) * direction
                            * p["volume_lot"] * CONTRACT_PER_LOT, 2)
                self._closed.append({
                    "deal_ticket": abs(hash((ticket, close_px))) % (10 ** 9),
                    "position_ticket": ticket,
                    "symbol": p["symbol"],
                    "trade_type": f"{p['side']} (Close)",
                    "volume": p["volume_lot"],
                    "close_time": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    "close_price": close_px, "profit": pnl, "commission": 0.0,
                    "swap": 0.0, "net_profit": pnl,
                })
                done.append(ticket)
            for t in done:
                self._opens.pop(t, None)
        for t in done:
            print(f"[PAPER FILL] #{t} ditutup.")
