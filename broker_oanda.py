"""Adapter OANDA v20 REST (API key, practice/demo atau live, tanpa terminal)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from broker_base import BrokerAdapter, NormalizedPosition, Tick
from config import (
    LOCAL_TZ,
    OANDA_ACCOUNT_ID,
    OANDA_API_KEY,
    OANDA_INSTRUMENT,
    OANDA_UNITS_PER_LOT,
    oanda_base_url,
)


def _headers():
    return {"Authorization": f"Bearer {OANDA_API_KEY}", "Content-Type": "application/json"}


def _fmt_price(p: float) -> str:
    # XAU_USD presisi 3 desimal di OANDA; 2 desimal strategi tetap valid.
    return f"{float(p):.3f}"


class OandaAdapter(BrokerAdapter):
    name = "oanda"

    def __init__(self, api_key=None, account_id=None, env=None, instrument=None, risk_percent=None):
        from config import OANDA_ENV

        self.api_key = api_key or OANDA_API_KEY
        self.account_id = account_id or OANDA_ACCOUNT_ID
        self.env = (env or OANDA_ENV or "practice").lower()
        self.instrument = instrument or OANDA_INSTRUMENT
        self.risk_percent = risk_percent
        self.base = oanda_base_url(self.env)
        self._s = requests.Session()
        self._s.headers.update({"Authorization": f"Bearer {self.api_key}",
                                "Content-Type": "application/json"})

    # ---------- koneksi ----------
    def initialize(self) -> bool:
        try:
            r = self._s.get(f"{self.base}/accounts/{self.account_id}/summary", timeout=10)
            if r.status_code != 200:
                print(f"[ERROR] OANDA summary {r.status_code}: {r.text[:200]}")
                return False
            bal = r.json().get("account", {}).get("balance")
            print(f"[OANDA] konek {self.env} {self.account_id} bal={bal}")
            return True
        except Exception as e:
            print(f"[ERROR] OANDA init: {e}")
            return False

    # ---------- data ----------
    def get_rates_m15(self, symbol: str | None = None, count: int = 100):
        inst = symbol or self.instrument
        # OANDA pakai XAU_USD; terima juga XAUUSD dari kode lama.
        if inst == "XAUUSD":
            inst = "XAU_USD"
        try:
            r = self._s.get(
                f"{self.base}/accounts/{self.account_id}/instruments/{inst}/candles",
                params={"granularity": "M15", "count": min(int(count), 5000), "price": "M"},
                timeout=15)
            r.raise_for_status()
            rows = []
            for c in r.json().get("candles", []):
                if not c.get("complete", True):
                    continue
                mid = c.get("mid") or {}
                try:
                    ts = pd.to_datetime(c["time"], utc=True).timestamp()
                except Exception:
                    continue
                rows.append({
                    "time": int(ts),
                    "open": float(mid["o"]), "high": float(mid["h"]),
                    "low": float(mid["l"]), "close": float(mid["c"]),
                    "tick_volume": int(c.get("volume", 0)),
                })
            if not rows:
                return None
            return pd.DataFrame(rows)
        except Exception as e:
            print(f"[ERROR] OANDA candles: {e}")
            return None

    def get_balance(self) -> float:
        try:
            r = self._s.get(f"{self.base}/accounts/{self.account_id}/summary", timeout=10)
            r.raise_for_status()
            return float(r.json().get("account", {}).get("balance", 0.0))
        except Exception:
            return 0.0

    def get_tick(self, symbol: str | None = None) -> Tick | None:
        inst = symbol or self.instrument
        if inst == "XAUUSD":
            inst = "XAU_USD"
        try:
            r = self._s.get(f"{self.base}/accounts/{self.account_id}/pricing",
                            params={"instruments": inst}, timeout=10)
            r.raise_for_status()
            prices = r.json().get("prices", [])
            if not prices:
                return None
            p = prices[0]
            bid = float((p.get("bids") or [{}])[0].get("price", 0))
            ask = float((p.get("asks") or [{}])[0].get("price", 0))
            if not bid or not ask:
                # fallback mid
                mid = float(p.get("closeoutBid", bid) or 0)
                if mid:
                    return Tick(bid=mid, ask=mid)
                return None
            return Tick(bid=bid, ask=ask)
        except Exception as e:
            print(f"[ERROR] OANDA pricing: {e}")
            return None

    # ---------- order ----------
    def _lot_to_units(self, lot: float, action: str) -> str:
        units = int(round(float(lot) * OANDA_UNITS_PER_LOT))
        units = max(1, units)
        return str(units if action == "BUY" else -units)

    def _units_to_lot(self, units) -> float:
        try:
            return round(abs(float(units)) / OANDA_UNITS_PER_LOT, 2)
        except Exception:
            return 0.01

    def calculate_lot(self, entry: float, sl: float) -> float:
        balance = self.get_balance()
        if not balance or balance <= 0:
            return 0.01
        dist = abs(entry - sl)
        if dist <= 0:
            return 0.01
        lot = round(balance * self._risk() / (dist * 100), 2)
        # OANDA min 1 unit = 0.01 lot
        return max(0.01, lot)

    def market_order(self, symbol: str | None, action: str, lot: float, sl: float, tp: float):
        inst = symbol or self.instrument
        if inst == "XAUUSD":
            inst = "XAU_USD"
        units = self._lot_to_units(lot, action)
        body = {"order": {
            "instrument": inst, "units": units, "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": _fmt_price(sl), "timeInForce": "GTC"},
            "takeProfitOnFill": {"price": _fmt_price(tp), "timeInForce": "GTC"},
            "clientExtensions": {"comment": "Telegram Semi-Auto"},
        }}
        try:
            r = self._s.post(f"{self.base}/accounts/{self.account_id}/orders",
                             json=body, timeout=15)
            if r.status_code not in (200, 201):
                return False, f"OANDA menolak ({r.status_code}: {r.text[:200]})"
            j = r.json()
            fill = j.get("orderFillTransaction") or {}
            price = fill.get("price") or fill.get("fullPrice") or ""
            tid = fill.get("tradeOpened", {}).get("tradeID") or fill.get("id", "")
            return True, f"Trade #{tid} @ {price} ({units} units)"
        except Exception as e:
            return False, f"OANDA order gagal: {e}"

    def list_positions(self, symbol: str | None = None) -> list[NormalizedPosition]:
        inst = symbol or self.instrument
        if inst == "XAUUSD":
            inst = "XAU_USD"
        try:
            r = self._s.get(f"{self.base}/accounts/{self.account_id}/openTrades", timeout=10)
            r.raise_for_status()
            out = []
            for t in r.json().get("trades", []):
                if t.get("instrument") != inst:
                    continue
                cur = float(t.get("currentUnits", 0))
                if cur == 0:
                    continue
                side = "BUY" if cur > 0 else "SELL"
                sl_o = t.get("stopLossOrder") or {}
                tp_o = t.get("takeProfitOrder") or {}
                try:
                    sl = float(sl_o.get("price", 0) or 0)
                except Exception:
                    sl = 0.0
                try:
                    tp = float(tp_o.get("price", 0) or 0)
                except Exception:
                    tp = 0.0
                out.append(NormalizedPosition(
                    ticket=str(t.get("id")), symbol=inst, side=side,
                    volume_lot=self._units_to_lot(cur),
                    price_open=float(t.get("price", 0) or 0),
                    sl=sl, tp=tp))
            return out
        except Exception as e:
            print(f"[ERROR] OANDA openTrades: {e}")
            return []

    def modify_sltp(self, ticket, symbol: str | None, new_sl: float, new_tp: float) -> bool:
        try:
            body = {
                "stopLoss": {"price": _fmt_price(new_sl), "timeInForce": "GTC"},
                "takeProfit": {"price": _fmt_price(new_tp), "timeInForce": "GTC"},
            }
            r = self._s.put(
                f"{self.base}/accounts/{self.account_id}/trades/{ticket}/orders",
                json=body, timeout=10)
            return r.status_code in (200, 201)
        except Exception:
            return False

    def partial_close(self, pos: NormalizedPosition, ratio: float = 0.5):
        try:
            # ambil units terkini dari server agar presisi
            r = self._s.get(f"{self.base}/accounts/{self.account_id}/trades/{pos.ticket}",
                            timeout=10)
            cur = 0.0
            if r.status_code == 200:
                cur = float(r.json().get("trade", {}).get("currentUnits", 0) or 0)
            if cur == 0:
                # fallback dari lot lokal
                units_close = max(1, int(round(pos.volume_lot * OANDA_UNITS_PER_LOT * ratio)))
            else:
                units_close = int(abs(cur) * ratio)
                units_close = max(1, units_close)
                if units_close >= abs(cur):
                    # jangan tutup habis via partial; sisakan 1 unit
                    units_close = int(abs(cur)) - 1
                    if units_close < 1:
                        return False, 0.0
            rc = self._s.put(
                f"{self.base}/accounts/{self.account_id}/trades/{pos.ticket}/close",
                json={"units": str(units_close)}, timeout=10)
            if rc.status_code in (200, 201):
                return True, round(units_close / OANDA_UNITS_PER_LOT, 2)
            # retry dengan tanda negatif untuk sisi SELL (kompatibilitas)
            rc2 = self._s.put(
                f"{self.base}/accounts/{self.account_id}/trades/{pos.ticket}/close",
                json={"units": str(-units_close)}, timeout=10)
            if rc2.status_code in (200, 201):
                return True, round(units_close / OANDA_UNITS_PER_LOT, 2)
            print(f"[SKIP PARTIAL] OANDA #{pos.ticket}: {rc.status_code} {rc.text[:150]}")
            return False, 0.0
        except Exception as e:
            print(f"[ERROR] OANDA partial: {e}")
            return False, 0.0

    def fetch_closed(self, days_back: int = 7) -> list[dict]:
        try:
            now = datetime.now(timezone.utc)
            frm = (now - timedelta(days=days_back)).isoformat()
            r = self._s.get(f"{self.base}/accounts/{self.account_id}/transactions",
                            params={"from": frm, "to": now.isoformat(),
                                    "pageSize": 1000, "type": "ORDER_FILL"},
                            timeout=15)
            r.raise_for_status()
            rows = []
            for tx in r.json().get("transactions", []):
                if tx.get("type") != "ORDER_FILL":
                    continue
                # hanya fill penutup (ada P/L atau reason close)
                reason = str(tx.get("reason", ""))
                pl_raw = tx.get("pl")
                if pl_raw is None and "CLOSE" not in reason and "TAKE_PROFIT" not in reason \
                        and "STOP_LOSS" not in reason and "TRAILING" not in reason:
                    continue
                try:
                    pl = float(pl_raw or 0)
                except Exception:
                    pl = 0.0
                try:
                    comm = float(tx.get("commission") or 0)
                except Exception:
                    comm = 0.0
                try:
                    fin = float(tx.get("financing") or 0)
                except Exception:
                    fin = 0.0
                units = tx.get("units") or tx.get("tradeReduced", {}).get("units") or 0
                trade_id = (tx.get("tradeReduced", {}) or {}).get("tradeID") or tx.get("tradeID") or tx.get("id")
                try:
                    t = pd.to_datetime(tx.get("time"), utc=True).tz_convert(LOCAL_TZ)
                    close_time = t.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    close_time = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
                try:
                    deal_id = int(str(tx.get("id")).split("-")[-1])
                except Exception:
                    deal_id = abs(hash(str(tx.get("id")))) % (10 ** 9)
                rows.append({
                    "deal_ticket": deal_id,
                    "position_ticket": str(trade_id),
                    "symbol": tx.get("instrument", self.instrument),
                    "trade_type": "BUY (Close)" if float(units or 0) < 0 else "SELL (Close)",
                    "volume": self._units_to_lot(units or 0),
                    "close_time": close_time,
                    "close_price": float(tx.get("price") or tx.get("fullPrice") or 0),
                    "profit": pl, "commission": comm, "swap": fin,
                    "net_profit": round(pl + comm + fin, 2),
                })
            return rows
        except Exception as e:
            print(f"[ERROR] OANDA history: {e}")
            return []
