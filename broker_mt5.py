"""Adapter MT5 terminal desktop (jalur lama, Windows + terminal terbuka)."""
from __future__ import annotations

import pandas as pd

from broker_base import BrokerAdapter, NormalizedPosition, Tick
from config import (
    DEVIATION,
    MAGIC_NUMBER,
)


def _mt5():
    import MetaTrader5 as mt5

    return mt5


class MT5Adapter(BrokerAdapter):
    name = "mt5"

    def __init__(self, login=None, password=None, server=None, path=None, risk_percent=None):
        # None = ikuti global .env (kompatibel single-user lama).
        self._login = login
        self._password = password
        self._server = server
        self._path = path
        self.risk_percent = risk_percent
        if self._login is None and self._password is None and self._server is None:
            from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH

            self._login, self._password, self._server, self._path = (
                MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH)

    def initialize(self) -> bool:
        mt5 = _mt5()
        if self._login and self._password and self._server:
            kw = {"login": self._login, "password": self._password, "server": self._server}
            if self._path:
                kw["path"] = self._path
            return bool(mt5.initialize(**kw))
        return bool(mt5.initialize())

    def shutdown(self):
        try:
            _mt5().shutdown()
        except Exception:
            pass

    def get_rates_m15(self, symbol: str, count: int = 100):
        return self.get_rates(symbol, "M15", count)

    def get_rates(self, symbol: str, timeframe: str = "M15", count: int = 100):
        mt5 = _mt5()
        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        tf = tf_map.get((timeframe or "M15").upper(), mt5.TIMEFRAME_M15)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None:
            return None
        return pd.DataFrame(rates)

    def get_balance(self) -> float:
        mt5 = _mt5()
        acct = mt5.account_info()
        return float(acct.balance) if acct else 0.0

    def get_tick(self, symbol: str) -> Tick | None:
        mt5 = _mt5()
        t = mt5.symbol_info_tick(symbol)
        if not t:
            return None
        return Tick(bid=float(t.bid), ask=float(t.ask))

    def calculate_lot(self, entry: float, sl: float) -> float:
        mt5 = _mt5()
        try:
            acct = mt5.account_info()
        except Exception:
            acct = None
        if acct is None:
            return 0.01
        dist = abs(entry - sl)
        if dist <= 0:
            return 0.01
        return max(0.01, round(acct.balance * self._risk() / (dist * 100), 2))

    def _filling_mode(self, symbol: str):
        mt5 = _mt5()
        sym = mt5.symbol_info(symbol)
        if not sym:
            return mt5.ORDER_FILLING_RETURN
        if sym.filling_mode & mt5.ORDER_FILLING_IOC:
            return mt5.ORDER_FILLING_IOC
        if sym.filling_mode & mt5.ORDER_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def market_order(self, symbol: str, action: str, lot: float, sl: float, tp: float):
        mt5 = _mt5()
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return False, "Tick broker tak tersedia"
        is_buy = action == "BUY"
        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot),
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": tick.ask if is_buy else tick.bid,
            "sl": float(sl),
            "tp": float(tp),
            "deviation": DEVIATION,
            "magic": MAGIC_NUMBER,
            "comment": "Telegram Semi-Auto",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(symbol),
        })
        if result is None:
            return False, f"order_send gagal: {mt5.last_error()}"
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return False, f"Broker menolak ({result.retcode}: {result.comment})"
        return True, f"Ticket #{result.order} @ {result.price:.2f}"

    def list_positions(self, symbol: str | None = None) -> list[NormalizedPosition]:
        mt5 = _mt5()
        out: list[NormalizedPosition] = []
        try:
            positions = mt5.positions_get() or []
        except Exception:
            return []
        for p in positions:
            if p.magic != MAGIC_NUMBER:
                continue
            if symbol and p.symbol != symbol:
                continue
            side = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
            out.append(NormalizedPosition(
                ticket=p.ticket, symbol=p.symbol, side=side,
                volume_lot=float(p.volume), price_open=float(p.price_open),
                sl=float(p.sl or 0.0), tp=float(p.tp or 0.0),
            ))
        return out

    def modify_sltp(self, ticket, symbol: str, new_sl: float, new_tp: float) -> bool:
        mt5 = _mt5()
        r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP,
                            "position": ticket, "symbol": symbol,
                            "sl": float(round(new_sl, 2)),
                            "tp": float(round(new_tp, 2))})
        return r is not None and r.retcode == mt5.TRADE_RETCODE_DONE

    def partial_close(self, pos: NormalizedPosition, ratio: float = 0.5):
        mt5 = _mt5()
        live = None
        try:
            for p in (mt5.positions_get() or []):
                if p.ticket == pos.ticket:
                    live = p
                    break
        except Exception:
            return False, 0.0
        if live is None:
            return False, 0.0
        sym = mt5.symbol_info(live.symbol)
        close_vol = round(round(live.volume * ratio / sym.volume_step)
                          * sym.volume_step, 2)
        if close_vol < sym.volume_min or (live.volume - close_vol) < sym.volume_min:
            return False, 0.0
        is_buy = live.type == mt5.POSITION_TYPE_BUY
        tick = mt5.symbol_info_tick(live.symbol)
        r = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "position": live.ticket,
            "symbol": live.symbol, "volume": close_vol,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if is_buy else tick.ask,
            "deviation": DEVIATION, "magic": MAGIC_NUMBER,
            "comment": "Partial TP 50%",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(live.symbol),
        })
        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
            return True, close_vol
        return False, 0.0

    def fetch_closed(self, days_back: int = 7) -> list[dict]:
        from datetime import datetime, timedelta

        from config import LOCAL_TZ

        mt5 = _mt5()
        try:
            deals = mt5.history_deals_get(datetime.now() - timedelta(days=days_back),
                                          datetime.now())
        except Exception:
            return []
        if not deals:
            return []
        rows = []
        for d in deals:
            if d.magic != MAGIC_NUMBER or d.entry != mt5.DEAL_ENTRY_OUT:
                continue
            rows.append({
                "deal_ticket": d.ticket,
                "position_ticket": d.position_id,
                "symbol": d.symbol,
                "trade_type": "BUY (Close)" if d.type == mt5.DEAL_TYPE_SELL else "SELL (Close)",
                "volume": d.volume,
                "close_time": datetime.fromtimestamp(d.time, tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "close_price": d.price,
                "profit": d.profit,
                "commission": d.commission,
                "swap": d.swap,
                "net_profit": round(d.profit + d.commission + d.swap, 2),
            })
        return rows
