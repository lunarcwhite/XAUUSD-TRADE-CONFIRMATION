"""Regresi temuan review 2026-10-07 (C1-C4, W1/W5). Stdlib + stub, tanpa jaringan."""
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from zoneinfo import ZoneInfo

import config
import state_manager as sm

TZ = ZoneInfo("Asia/Jakarta")


class StubBroker:
    """Broker stub: fetch_closed + close_position + posisi programmable."""
    name = "stub"

    def __init__(self, closed_rows=None, positions=None, tick=None):
        self._rows = closed_rows or []
        self._positions = positions
        self._tick = tick
        self.closed = []

    def fetch_closed(self, days_back=7):
        return [dict(r) for r in self._rows]

    def list_positions(self, symbol=None):
        return self._positions

    def get_tick(self, symbol=None):
        return self._tick

    def partial_close(self, pos, ratio=0.5):
        return False, 0.0

    def close_position(self, pos):
        self.closed.append(str(getattr(pos, "ticket", "?")))
        return True, 1.0

    def get_balance(self):
        return 10000.0


def _row(ticket, pos_ticket, pnl, mode="INTRADAY", day=None):
    day = day or datetime.now(TZ).strftime("%Y-%m-%d")
    return {"deal_ticket": ticket, "position_ticket": pos_ticket, "symbol": "XAUUSD",
            "trade_type": "BUY", "volume": 0.01, "close_time": day + " 12:00:00",
            "close_price": 2600, "profit": pnl, "commission": 0, "swap": 0,
            "net_profit": pnl, "mode_used": mode}


class TestC1LiveStats(unittest.TestCase):
    def setUp(self):
        import db_logger as L
        self.L = L
        self.tmp = os.path.join(tempfile.gettempdir(), "test_review_c1.db")
        if os.path.exists(self.tmp):
            os.remove(self.tmp)
        self._old = L.DB_NAME
        L.DB_NAME = self.tmp
        L.init_db()
        self.b = sm.BotStateManager("INTRADAY")

    def tearDown(self):
        self.L.DB_NAME = self._old
        if os.path.exists(self.tmp):
            os.remove(self.tmp)
        sm.bot_state.set_mode("SNIPER")

    def test_kill_fires_from_live_sync(self):
        # DB kosong (seperti jam 10 pagi sebelum reporter 23:55) + broker
        # baru saja menutup posisi rugi -250 -> kill-switch HARUS trip.
        broker = StubBroker(closed_rows=[_row(1, "p1", -250.0)])
        blocked, why = self.b.should_block_signal(
            user_id="uk", balance=10000, broker=broker, db_path=self.tmp,
            now=datetime(2026, 1, 5, 15, 0, tzinfo=TZ))
        self.assertTrue(blocked, "kill-switch harus trip dari sync live")
        self.assertIn("KILL", why)

    def test_quota_counts_positions_not_deals(self):
        # partial + close final SATU posisi = 1 kuota, bukan 2.
        broker = StubBroker(closed_rows=[
            _row(11, "p1", 5.0), _row(12, "p1", 3.0),
            _row(13, "p2", 1.0), _row(14, "p3", 1.0)])
        blocked, _ = self.b.should_block_signal(
            user_id="uq", balance=10000, broker=broker, db_path=self.tmp,
            now=datetime(2026, 1, 5, 15, 0, tzinfo=TZ))
        self.assertTrue(blocked, "3 posisi distinct = kuota penuh")
        broker2 = StubBroker(closed_rows=[_row(21, "q1", 5.0), _row(22, "q1", 3.0)])
        blocked2, _ = self.b.should_block_signal(
            user_id="uq2", balance=10000, broker=broker2, db_path=self.tmp,
            now=datetime(2026, 1, 5, 15, 0, tzinfo=TZ))
        self.assertFalse(blocked2, "1 posisi (2 deal) != kuota")


class TestC3FullClose(unittest.TestCase):
    def test_paper_full_close(self):
        from broker_paper import PaperAdapter
        from broker_base import NormalizedPosition
        b = PaperAdapter(balance=10000)
        b._opens["T1"] = {"symbol": "XAUUSD", "side": "BUY", "volume_lot": 0.05,
                          "price_open": 2600.0, "sl": 2590.0, "tp": 2620.0,
                          "opened_at": "x"}
        b._last_close = 2601.0
        pos = NormalizedPosition("T1", "XAUUSD", "BUY", 0.05, 2600.0, 2590.0, 2620.0)
        self.assertEqual(b.partial_close(pos, 1.0), (False, 0.0))  # guard lama menolak
        ok, lot = b.close_position(pos)
        self.assertTrue(ok)
        self.assertEqual(lot, 0.05)
        self.assertNotIn("T1", b._opens)
        self.assertEqual(len(b.fetch_closed()), 1)

    def test_autoflat_unknown_retries(self):
        b = sm.BotStateManager("INTRADAY")
        closed, failed, _ = b.close_all_positions(
            broker=StubBroker(positions=None), symbol="XAUUSD")
        self.assertEqual((closed, failed), (0, 1), "unknown -> failed agar retry")
        ok_broker = StubBroker(positions=[])
        closed, failed, _ = b.close_all_positions(
            broker=ok_broker, symbol="XAUUSD")
        self.assertEqual((closed, failed), (0, 0))


class TestC4CTraderFilter(unittest.TestCase):
    def test_other_symbols_excluded(self):
        from broker_ctrader import CTraderAdapter
        ad = object.__new__(CTraderAdapter)
        ad._symbol_id = 1
        ad._digits = 2
        ad.symbol_name = "XAUUSD"

        class TD:
            def __init__(self, sid):
                self.symbolId = sid
                self.tradeSide = 1
                self.volume = 1000
                self.label = "x"

        class P:
            def __init__(self, pid, sid):
                self.positionId = pid
                self.tradeData = TD(sid)
                self.price = 260000
                self.stopLoss = 0
                self.takeProfit = 0
                self.positionStatus = 1

        ad._positions = lambda: [P(101, 1), P(102, 999)]
        for sym in ("XAUUSD", None):
            out = ad.list_positions(sym)
            self.assertEqual([p.ticket for p in out], ["101"],
                             f"simbol lain bocor saat symbol={sym!r}")

    def test_positions_none_propagates(self):
        from broker_ctrader import CTraderAdapter
        ad = object.__new__(CTraderAdapter)
        ad._symbol_id = 1
        ad._positions = lambda: None
        self.assertIsNone(ad.list_positions("XAUUSD"))


class TestC2Encryption(unittest.TestCase):
    def test_roundtrip(self):
        import cred_crypto as cc
        old = config.TELEGRAM_BOT_TOKEN
        config.TELEGRAM_BOT_TOKEN = "123456:TESTTOKEN_explicit_for_unittest"
        try:
            ct = cc.encrypt_value("secret-token-abc")
            self.assertTrue(cc.is_encrypted(ct))
            self.assertNotIn("secret-token", ct)
            self.assertEqual(cc.decrypt_value(ct), "secret-token-abc")
            self.assertEqual(cc.decrypt_value("plaintext-lama"), "plaintext-lama")
        finally:
            config.TELEGRAM_BOT_TOKEN = old

    def test_user_store_roundtrip(self):
        import user_store as us
        old_token, old_db = config.TELEGRAM_BOT_TOKEN, config.USERS_DB
        tmp = os.path.join(tempfile.gettempdir(), "test_review_users.db")
        if os.path.exists(tmp):
            os.remove(tmp)
        config.TELEGRAM_BOT_TOKEN = "123456:TESTTOKEN_explicit_for_unittest"
        config.USERS_DB = tmp
        try:
            us.save_user("999", user_id="t1", broker_mode="paper",
                         ctrader_access_token="tok-xyz", enabled=1)
            import sqlite3
            raw = sqlite3.connect(tmp).execute(
                "SELECT ctrader_access_token FROM bot_users WHERE chat_id='999'").fetchone()[0]
            self.assertNotIn("tok-xyz", str(raw), "token plaintext di disk!")
            sess = us.to_session_user(us.get_user("999"))
            self.assertEqual(sess["ctrader_access_token"], "tok-xyz")
        finally:
            config.TELEGRAM_BOT_TOKEN, config.USERS_DB = old_token, old_db
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass


class TestW1FailClosed(unittest.TestCase):
    def test_no_tick_blocks(self):
        from order_manager import check_price_drift
        ok, msg = check_price_drift(
            {"entry": 2600.0, "action": "BUY", "symbol": "XAUUSD", "mode": "INTRADAY"},
            broker=StubBroker(tick=None))
        self.assertFalse(ok, "tanpa tick -> tolak (fail-closed)")
        self.assertIn("Dibatalkan", msg)


if __name__ == "__main__":
    unittest.main()
