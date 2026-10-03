"""Abstraksi broker: MT5 terminal vs OANDA REST API key (PRD dual-broker)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Tick:
    bid: float
    ask: float


@dataclass
class NormalizedPosition:
    ticket: str  # MT5: int tiket, OANDA: trade ID string
    symbol: str  # symbol/instrument yang dibuka
    side: str  # "BUY" atau "SELL"
    volume_lot: float  # dalam lot (OANDA dikonversi dari units)
    price_open: float
    sl: float
    tp: float


class BrokerAdapter(ABC):
    """Kontrak minimal yang dibutuhkan main/order_manager/db_logger."""

    name: str = "base"
    risk_percent: float | None = None

    @abstractmethod
    def initialize(self) -> bool:
        raise NotImplementedError

    def shutdown(self):
        return None

    @abstractmethod
    def get_rates_m15(self, symbol: str, count: int = 100):
        """Return pandas DataFrame: time,open,high,low,close,tick_volume. time=epoch detik."""
        raise NotImplementedError

    # ---------- Multi-Timeframe (MTF) ----------
    # timeframe: M1,M5,M15,H1,H4,D1. Default via get_rates_m15 untuk kompat lama.
    def get_rates(self, symbol: str, timeframe: str = "M15", count: int = 100):
        tf = (timeframe or "M15").upper()
        if tf == "M15":
            return self.get_rates_m15(symbol, count)
        # Adapter lama tanpa override -> fallback M15 agar tidak crash.
        return self.get_rates_m15(symbol, count)

    @abstractmethod
    def get_balance(self) -> float:
        raise NotImplementedError

    @abstractmethod
    def get_tick(self, symbol: str) -> Tick | None:
        raise NotImplementedError

    def _risk(self) -> float:
        try:
            r = float(self.risk_percent) if self.risk_percent else 0.0
            return r if r > 0 else __import__("config").RISK_PERCENT
        except Exception:
            from config import RISK_PERCENT

            return RISK_PERCENT

    def calculate_lot(self, entry: float, sl: float) -> float:
        balance = self.get_balance()
        if not balance or balance <= 0:
            return 0.01
        dist = abs(entry - sl)
        if dist <= 0:
            return 0.01
        return max(0.01, round(balance * self._risk() / (dist * 100), 2))

    @abstractmethod
    def market_order(self, symbol: str, action: str, lot: float, sl: float, tp: float):
        """Return (ok: bool, detail: str)."""
        raise NotImplementedError

    @abstractmethod
    def list_positions(self, symbol: str | None = None) -> list[NormalizedPosition]:
        raise NotImplementedError

    @abstractmethod
    def modify_sltp(self, ticket, symbol: str, new_sl: float, new_tp: float) -> bool:
        raise NotImplementedError

    @abstractmethod
    def partial_close(self, pos: NormalizedPosition, ratio: float = 0.5):
        """Return (ok: bool, closed_lot: float)."""
        raise NotImplementedError

    @abstractmethod
    def fetch_closed(self, days_back: int = 7) -> list[dict]:
        """Return list dict siap INSERT ke trade_history.

        Keys: deal_ticket, position_ticket, symbol, trade_type, volume,
              close_time (%Y-%m-%d %H:%M:%S), close_price, profit,
              commission, swap, net_profit
        """
        raise NotImplementedError


def create_broker(mode: str | None = None) -> BrokerAdapter:
    """Factory BROKER_MODE=mt5|oanda. Import lazy agar mode oanda jalan tanpa MT5."""
    from config import BROKER_MODE

    m = (mode or BROKER_MODE or "mt5").lower()
    if m == "paper":
        from broker_paper import PaperAdapter

        return PaperAdapter()
    if m == "oanda":
        from broker_oanda import OandaAdapter

        return OandaAdapter()
    if m == "ctrader":
        from broker_ctrader import CTraderAdapter

        return CTraderAdapter()
    from broker_mt5 import MT5Adapter

    return MT5Adapter()


def create_broker_for_user(user: dict) -> BrokerAdapter:
    """Factory per-user multi-user. user dari config.load_users()."""
    mode = str(user.get("broker_mode") or "mt5").lower()
    risk = user.get("risk_percent")
    if mode == "paper":
        from broker_paper import PaperAdapter

        return PaperAdapter(risk_percent=float(risk) if risk else None)
    if mode == "oanda":
        from broker_oanda import OandaAdapter

        b = OandaAdapter(
            api_key=user.get("oanda_api_key"),
            account_id=user.get("oanda_account_id"),
            env=user.get("oanda_env") or "practice",
            instrument=user.get("oanda_instrument") or "XAU_USD",
        )
        if risk:
            b.risk_percent = float(risk)
        return b
    if mode == "ctrader":
        from broker_ctrader import CTraderAdapter

        b = CTraderAdapter(
            access_token=user.get("ctrader_access_token"),
            account_id=user.get("ctrader_account_id"),
            env=user.get("ctrader_env") or "demo",
            symbol=user.get("ctrader_symbol") or "XAUUSD",
        )
        if risk:
            b.risk_percent = float(risk)
        return b
    from broker_mt5 import MT5Adapter

    b = MT5Adapter(
        login=user.get("mt5_login"),
        password=user.get("mt5_password"),
        server=user.get("mt5_server"),
        path=user.get("mt5_path"),
    )
    if risk:
        b.risk_percent = float(risk)
    return b
