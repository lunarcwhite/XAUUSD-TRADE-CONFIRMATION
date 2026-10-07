"""Adapter cTrader Open API (Spotware, OAuth2 + ProtoBuf/TCP, cross-OS).

Status: FULL IMPLEMENTASI, BELUM terverifikasi live (butuh token demo).
Asumsi yang wajib dikonfirmasi saat uji live:
- VOL_PER_LOT = 100_000 unit per 1.00 lot (divalidasi vs minVolume saat init).
- Uang (balance/profit/komisi) dalam sen (bagi 100, via moneyDigits bila ada).
- Harga raw dibagi 10**digits simbol.
- Fill order dideteksi via polling label posisi (bukan ExecutionEvent).

Alur auth: ApplicationAuth(clientId/secret server) -> AccountAuth(accountId,
accessToken user) -> TraderReq/SymbolsList/SymbolById/SubscribeSpots.
Twisted reactor jalan di 1 thread daemon bersama; semua I/O sinkron via Event.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pandas as pd

from broker_base import BrokerAdapter, NormalizedPosition, Tick
from config import LOCAL_TZ

VOL_PER_LOT = 100_000  # unit volume per 1.00 lot (verifikasi live!)
REQ_TIMEOUT = 15


def _ct():
    from ctrader_open_api import Client, EndPoints, TcpProtocol
    from ctrader_open_api.messages import OpenApiMessages_pb2 as Msg
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as Model
    return Client, EndPoints, TcpProtocol, Msg, Model


_reactor_lock = threading.Lock()
_reactor_started = False


def _ensure_reactor():
    global _reactor_started
    with _reactor_lock:
        if _reactor_started:
            return
        from twisted.internet import reactor
        t = threading.Thread(target=reactor.run,
                             kwargs={"installSignalHandlers": False}, daemon=True)
        t.start()
        _reactor_started = True


_TF_PERIOD = {"M1": 1, "M5": 5, "M15": 7, "H1": 9, "H4": 10, "D1": 12}


class CTraderAdapter(BrokerAdapter):
    name = "ctrader"

    def __init__(self, client_id=None, client_secret=None, access_token=None,
                 account_id=None, env=None, symbol=None, risk_percent=None):
        from config import (CTRADER_ACCESS_TOKEN, CTRADER_ACCOUNT_ID,
                            CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET,
                            CTRADER_ENV, CTRADER_SYMBOL)
        self.client_id = client_id or CTRADER_CLIENT_ID
        self.client_secret = client_secret or CTRADER_CLIENT_SECRET
        self.access_token = access_token or CTRADER_ACCESS_TOKEN
        try:
            self.account_id = int(account_id or CTRADER_ACCOUNT_ID or 0)
        except (TypeError, ValueError):
            self.account_id = 0
        self.env = (env or CTRADER_ENV or "demo").lower()
        self.symbol_name = (symbol or CTRADER_SYMBOL or "XAUUSD").upper()
        self.risk_percent = risk_percent
        self._lock = threading.Lock()
        self._client = None
        self._connected = threading.Event()
        self._spot: dict[int, tuple[float, float]] = {}
        self._order_errors: dict[str, str] = {}
        self._symbol_id = 0
        self._digits = 2
        self._lot_size = 100.0
        self._min_lot = 0.01
        self._step_lot = 0.01
        self._seq = 0

    # ---------- transport sinkron di atas Twisted ----------
    def _send(self, req, timeout=REQ_TIMEOUT):
        from twisted.internet import reactor
        _, _, _, Msg, _ = _ct()
        box: dict = {}
        done = threading.Event()

        def _ok(res):
            box["res"] = res
            done.set()

        def _err(fail):
            try:
                msg = fail.value
                desc = getattr(msg, "description", None) or str(msg)
            except Exception:
                desc = str(fail)
            box["err"] = desc
            done.set()

        def _do():
            try:
                d = self._client.send(req)
                d.addCallbacks(_ok, _err)
            except Exception as e:
                box["err"] = str(e)
                done.set()

        if threading.current_thread() is threading.main_thread():
            reactor.callFromThread(_do)
        else:
            try:
                reactor.callFromThread(_do)
            except Exception:
                _do()
        if not done.wait(timeout):
            return None, "timeout menunggu respons cTrader"
        if "err" in box:
            return None, box["err"]
        return box.get("res"), ""

    def _on_message(self, message):
        try:
            ptype = message.payloadType
        except Exception:
            return
        # SpotEvent ~ bid/ask live; OrderErrorEvent ~ penolakan order.
        try:
            _, _, _, Msg, _ = _ct()
            if ptype == Msg.ProtoOASpotEvent().payloadType and hasattr(message, "symbolId"):
                sid = int(message.symbolId)
                scale = 10 ** self._digits
                with self._lock:
                    self._spot[sid] = (float(message.bid) / scale,
                                       float(message.ask) / scale)
            elif ptype == Msg.ProtoOAOrderErrorEvent().payloadType:
                with self._lock:
                    self._order_errors["_last"] = (
                        f"{getattr(message, 'errorCode', '?')}: "
                        f"{getattr(message, 'description', '')}"[:200])
        except Exception:
            pass

    def _connect(self) -> tuple[bool, str]:
        Client, EndPoints, TcpProtocol, Msg, _ = _ct()
        from twisted.internet import reactor
        _ensure_reactor()
        host = (EndPoints.PROTOBUF_LIVE_HOST if self.env == "live"
                else EndPoints.PROTOBUF_DEMO_HOST)
        self._client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self._client.setMessageReceivedCallback(
            lambda _c, m: self._on_message(m))
        connected = threading.Event()

        def _on_connect(_c):
            connected.set()

        self._client.setConnectedCallback(_on_connect)
        reactor.callFromThread(self._client.startService)
        if not connected.wait(20):
            return False, "tak bisa konek host cTrader"
        req = Msg.ProtoOAApplicationAuthReq()
        req.clientId = self.client_id
        req.clientSecret = self.client_secret
        res, err = self._send(req)
        if res is None:
            return False, f"ApplicationAuth gagal: {err}"
        req = Msg.ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = self.account_id
        req.accessToken = self.access_token
        res, err = self._send(req)
        if res is None:
            return False, f"AccountAuth gagal: {err}"
        return True, "ok"

    # ---------- koneksi ----------
    def initialize(self) -> bool:
        print("[WARN] cTrader adapter BELUM terverifikasi live "
              "(butuh token demo untuk uji order).")
        if not self.client_id or not self.client_secret:
            print("[ERROR] CTRADER_CLIENT_ID/SECRET kosong.")
            return False
        if not self.access_token or not self.account_id:
            print("[ERROR] token/akun cTrader kosong.")
            return False
        try:
            _, _, _, Msg, _ = _ct()
        except ImportError:
            print("[ERROR] paket ctrader-open-api belum terinstal.")
            return False
        ok, detail = self._connect()
        if not ok:
            print(f"[ERROR] cTrader konek: {detail}")
            return False
        # Balance
        req = Msg.ProtoOATraderReq()
        req.ctidTraderAccountId = self.account_id
        res, err = self._send(req)
        if res is None:
            print(f"[ERROR] cTrader trader: {err}")
            return False
        # Simbol: cari ID dari nama
        req = Msg.ProtoOASymbolsListReq()
        req.ctidTraderAccountId = self.account_id
        res, err = self._send(req)
        if res is None:
            print(f"[ERROR] cTrader symbols: {err}")
            return False
        found = 0
        for s in list(res.symbol or []):
            try:
                if str(s.symbolName).upper() == self.symbol_name:
                    found = int(s.symbolId)
                    break
            except Exception:
                continue
        if not found:
            print(f"[ERROR] simbol {self.symbol_name} tak ada di broker ini.")
            return False
        self._symbol_id = found
        req = Msg.ProtoOASymbolByIdReq()
        req.ctidTraderAccountId = self.account_id
        req.symbolId = found
        res, err = self._send(req)
        if res is None:
            print(f"[ERROR] cTrader symbol: {err}")
            return False
        try:
            sym = res.symbol
            self._digits = int(sym.digits or 2)
            self._lot_size = float(sym.lotSize or 100.0)
            min_vol = float(sym.minVolume or 1000.0)
            step_vol = float(sym.stepVolume or 1000.0)
            self._min_lot = max(0.01, round(min_vol / VOL_PER_LOT, 2))
            self._step_lot = max(0.01, round(step_vol / VOL_PER_LOT, 2))
            if abs(self._min_lot - 0.01) > 0.05:
                print(f"[WARN] minVolume {min_vol} -> minLot {self._min_lot}; "
                      f"cek VOL_PER_LOT saat uji live!")
        except Exception as e:
            print(f"[ERROR] parse simbol: {e}")
            return False
        req = Msg.ProtoOASubscribeSpotsReq()
        req.ctidTraderAccountId = self.account_id
        req.symbolId = found
        self._send(req)  # best-effort; tick fallback via bar terakhir
        try:
            bal = self.get_balance()
        except Exception:
            bal = 0.0
        print(f"[CTRADER] konek {self.env} {self.account_id} "
              f"{self.symbol_name}#{found} bal={bal}")
        return True

    def list_accounts(self):
        """Akun milik token (untuk onboarding /start). Return [(id, is_live)]."""
        try:
            _, _, _, Msg, _ = _ct()
            _ensure_reactor()
            from config import CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET
            from ctrader_open_api import Client, EndPoints, TcpProtocol
            from twisted.internet import reactor
            holder: dict = {}
            done = threading.Event()
            client = Client(EndPoints.PROTOBUF_DEMO_HOST,
                            EndPoints.PROTOBUF_PORT, TcpProtocol)

            def _got(res):
                holder["res"] = res
                done.set()

            def _fail(fail):
                done.set()

            def _conn(_c):
                r = Msg.ProtoOAGetAccountListByAccessTokenReq()
                r.accessToken = self.access_token
                _c.send(r).addCallbacks(_got, _fail)

            client.setConnectedCallback(_conn)
            reactor.callFromThread(client.startService)
            if not done.wait(20):
                return []
            res = holder.get("res")
            if res is None:
                return []
            void = getattr(CTraderAdapter, "_void", None)
            _ = void, CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET
            return [(int(a.ctidTraderAccountId), bool(a.isLive))
                    for a in (res.ctidTraderAccount or [])]
        except Exception:
            return []

    # ---------- data ----------
    def _scale(self) -> float:
        return 10 ** self._digits

    def get_rates(self, symbol=None, timeframe="M15", count=100):
        from config import LOCAL_TZ as _TZ  # noqa (konsistensi zona)
        _ = _TZ
        _, _, _, Msg, _ = _ct()
        tf = _TF_PERIOD.get((timeframe or "M15").upper(), 7)
        req = Msg.ProtoOAGetTrendbarsReq()
        req.ctidTraderAccountId = self.account_id
        req.symbolId = self._symbol_id
        req.period = tf
        req.count = min(int(count), 5000)
        res, err = self._send(req)
        if res is None:
            print(f"[ERROR] cTrader bars {timeframe}: {err}")
            return None
        scale = self._scale()
        rows = []
        for b in (res.trendbar or []):
            try:
                ts = int(b.utcTimestampInMinutes) * 60
                low = float(b.low) / scale
                rows.append({
                    "time": ts,
                    "open": low + float(b.deltaOpen) / scale,
                    "high": low + float(b.deltaHigh) / scale,
                    "low": low,
                    "close": low + float(b.deltaClose) / scale,
                    "tick_volume": int(getattr(b, "volume", 0) or 0),
                })
            except Exception:
                continue
        if not rows:
            return None
        return pd.DataFrame(rows)

    def get_rates_m15(self, symbol=None, count=100):
        return self.get_rates(symbol, "M15", count)

    def get_balance(self) -> float:
        _, _, _, Msg, _ = _ct()
        req = Msg.ProtoOATraderReq()
        req.ctidTraderAccountId = self.account_id
        res, err = self._send(req, timeout=10)
        if res is None:
            return 0.0
        try:
            t = res.trader
            div = 10 ** int(getattr(t, "moneyDigits", 2) or 2)
            return float(t.balance) / div
        except Exception:
            return 0.0

    def get_tick(self, symbol=None) -> Tick | None:
        with self._lock:
            q = self._spot.get(self._symbol_id)
        if q:
            return Tick(bid=q[0], ask=q[1])
        df = self.get_rates(symbol, "M15", 2)
        if df is None or len(df) == 0:
            return None
        px = float(df.iloc[-1]["close"])
        return Tick(bid=round(px - 0.15, 2), ask=round(px + 0.15, 2))

    # ---------- order ----------
    def _to_lot(self, volume_units) -> float:
        try:
            return round(float(volume_units) / VOL_PER_LOT, 2)
        except Exception:
            return 0.01

    def _to_units(self, lot: float) -> int:
        units = int(round(float(lot) * VOL_PER_LOT))
        return max(int(self._min_lot * VOL_PER_LOT), units)

    def calculate_lot(self, entry: float, sl: float) -> float:
        balance = self.get_balance()
        if not balance or balance <= 0:
            return self._min_lot
        dist = abs(entry - sl)
        if dist <= 0:
            return self._min_lot
        raw = balance * self._risk() / (dist * self._lot_size)
        steps = max(1, int(raw / self._step_lot))
        return max(self._min_lot, round(steps * self._step_lot, 2))

    def _raw_price(self, price: float) -> int:
        return int(round(float(price) * self._scale()))

    def _positions(self) -> list | None:
        """Posisi live aktif. None = transport/API error (bedakan dari kosong).

        Fail-closed (review W5): pemanggil wajib perlakukan None sebagai
        "tak diketahui", bukan "tak ada posisi".
        """
        _, _, _, Msg, _ = _ct()
        req = Msg.ProtoOAReconcileReq()
        req.ctidTraderAccountId = self.account_id
        res, err = self._send(req)
        if res is None:
            return None
        return [p for p in (res.position or [])
                if int(getattr(p, "positionStatus", 0)) == 1]

    def market_order(self, symbol, action: str, lot: float, sl: float, tp: float):
        _, _, _, Msg, Model = _ct()
        with self._lock:
            self._seq += 1
            label = f"TSA-{int(time.time())}-{self._seq}"
        req = Msg.ProtoOANewOrderReq()
        req.ctidTraderAccountId = self.account_id
        req.symbolId = self._symbol_id
        req.orderType = Model.ProtoOAOrderType.Value("MARKET")
        req.tradeSide = Model.ProtoOATradeSide.Value(action)
        req.volume = self._to_units(lot)
        req.stopLoss = self._raw_price(sl)
        req.takeProfit = self._raw_price(tp)
        req.label = label
        req.comment = "Telegram Semi-Auto"
        res, err = self._send(req)
        if res is None:
            with self._lock:
                last = self._order_errors.pop("_last", "")
            return False, f"cTrader menolak ({err}{(' '+last) if last else ''})"
        # Fill via polling label (ExecutionEvent tidak diandalkan).
        deadline = time.time() + 12
        while time.time() < deadline:
            poss = self._positions()
            if poss is None:
                time.sleep(1)
                continue  # status tak diketahui -> polling lagi, bukan gagal
            for p in poss:
                try:
                    if str(p.tradeData.label) == label:
                        px = float(p.price) / self._scale()
                        return True, (f"Pos #{p.positionId} @ {px:.2f} "
                                       f"({lot} lot)")
                except Exception:
                    continue
            with self._lock:
                last = self._order_errors.get("_last")
            if last:
                return False, f"cTrader menolak ({last})"
            time.sleep(1)
        return False, "Timeout konfirmasi fill (cek manual posisi!)"

    def list_positions(self, symbol=None) -> list[NormalizedPosition] | None:
        """Posisi simbol adapter ini. None = status tak diketahui (fail-closed).

        Filter simbol SELALU diterapkan (review C4): tanpa filter, posisi
        simbol lain ikut terdaftar berlabel XAUUSD dan berisiko ikut di-flat.
        """
        out = []
        poss = self._positions()
        if poss is None:
            return None
        for p in poss:
            try:
                td = p.tradeData
                if int(td.symbolId) != self._symbol_id:
                    continue
                side = "BUY" if int(td.tradeSide) == 1 else "SELL"
                scale = self._scale()
                out.append(NormalizedPosition(
                    ticket=str(p.positionId), symbol=self.symbol_name,
                    side=side, volume_lot=self._to_lot(td.volume),
                    price_open=float(p.price) / scale,
                    sl=float(p.stopLoss or 0) / scale,
                    tp=float(p.takeProfit or 0) / scale))
            except Exception:
                continue
        return out

    def modify_sltp(self, ticket, symbol, new_sl: float, new_tp: float) -> bool:
        _, _, _, Msg, _ = _ct()
        req = Msg.ProtoOAAmendPositionSLTPReq()
        req.ctidTraderAccountId = self.account_id
        req.positionId = int(ticket)
        req.stopLoss = self._raw_price(new_sl)
        req.takeProfit = self._raw_price(new_tp)
        res, err = self._send(req)
        return res is not None

    def partial_close(self, pos: NormalizedPosition, ratio: float = 0.5):
        _, _, _, Msg, _ = _ct()
        try:
            live = None
            poss = self._positions()
            if poss is None:
                print(f"[SKIP PARTIAL] cTrader #{pos.ticket}: status posisi tak diketahui")
                return False, 0.0
            for p in poss:
                if str(p.positionId) == str(pos.ticket):
                    live = p
                    break
            if live is None:
                return False, 0.0
            cur_units = int(live.tradeData.volume)
            step = int(self._step_lot * VOL_PER_LOT)
            close_units = int(cur_units * ratio // step * step)
            min_u = int(self._min_lot * VOL_PER_LOT)
            if close_units < min_u or (cur_units - close_units) < min_u:
                return False, 0.0
            req = Msg.ProtoOAClosePositionReq()
            req.ctidTraderAccountId = self.account_id
            req.positionId = int(live.positionId)
            req.volume = close_units
            res, err = self._send(req)
            if res is None:
                print(f"[SKIP PARTIAL] cTrader #{pos.ticket}: {err}")
                return False, 0.0
            return True, round(close_units / VOL_PER_LOT, 2)
        except Exception as e:
            print(f"[ERROR] cTrader partial: {e}")
            return False, 0.0

    def close_position(self, pos: NormalizedPosition):
        """Tutup penuh: ProtoOAClosePositionReq TANPA volume = seluruh posisi.

        Dipakai auto-flat 23:00 (review C3) — partial_close(1.0) selalu
        ditolak guard sisa-minimum, jadi butuh close native.
        """
        _, _, _, Msg, _ = _ct()
        try:
            poss = self._positions()
            if poss is None:
                print(f"[SKIP CLOSE] cTrader #{pos.ticket}: status posisi tak diketahui")
                return False, 0.0
            live = None
            for p in poss:
                if str(p.positionId) == str(pos.ticket):
                    live = p
                    break
            if live is None:
                return False, 0.0
            lot = self._to_lot(int(live.tradeData.volume))
            req = Msg.ProtoOAClosePositionReq()
            req.ctidTraderAccountId = self.account_id
            req.positionId = int(live.positionId)
            res, err = self._send(req)
            if res is None:
                print(f"[SKIP CLOSE] cTrader #{pos.ticket}: {err}")
                return False, 0.0
            return True, lot
        except Exception as e:
            print(f"[ERROR] cTrader close: {e}")
            return False, 0.0

    def fetch_closed(self, days_back: int = 7) -> list[dict]:
        _, _, _, Msg, _ = _ct()
        try:
            now = datetime.now(LOCAL_TZ)
            frm = now - timedelta(days=days_back)
            req = Msg.ProtoOADealListReq()
            req.ctidTraderAccountId = self.account_id
            req.fromTimestamp = int(frm.timestamp() * 1000)
            req.toTimestamp = int(now.timestamp() * 1000)
            req.maxRows = 1000
            res, err = self._send(req)
            if res is None:
                print(f"[ERROR] cTrader deals: {err}")
                return []
            scale = self._scale()
            rows = []
            for d in (res.deal or []):
                try:
                    cpd = d.closePositionDetail
                    if cpd is None or int(getattr(cpd, "closedVolume", 0) or 0) <= 0:
                        continue  # bukan leg penutup
                    md = int(getattr(cpd, "moneyDigits",
                                     getattr(d, "moneyDigits", 2)) or 2)
                    div = 10 ** md
                    profit = float(cpd.grossProfit or 0) / div
                    comm = float(cpd.commission or 0) / div
                    swap = float(cpd.swap or 0) / div
                    t = datetime.fromtimestamp(
                        int(d.executionTimestamp) / 1000, tz=LOCAL_TZ)
                    side_close = int(d.tradeSide)
                    rows.append({
                        "deal_ticket": int(d.dealId),
                        "position_ticket": str(d.positionId),
                        "symbol": self.symbol_name,
                        "trade_type": ("BUY (Close)" if side_close == 2
                                       else "SELL (Close)"),
                        "volume": self._to_lot(cpd.closedVolume),
                        "close_time": t.strftime("%Y-%m-%d %H:%M:%S"),
                        "close_price": float(d.executionPrice) / scale,
                        "profit": profit, "commission": comm, "swap": swap,
                        "net_profit": round(profit + comm + swap, 2),
                    })
                except Exception:
                    continue
            return rows
        except Exception as e:
            print(f"[ERROR] cTrader history: {e}")
            return []
